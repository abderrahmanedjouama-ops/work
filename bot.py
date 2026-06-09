import os
import json
import random
import logging
import asyncio
import google.generativeai as genai

from datetime import datetime, timedelta
from pathlib import Path
from io import BytesIO

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)

from config import BOT_TOKEN, FILES_FOLDER, COOLDOWN_HOURS, GEMINI_API_KEY, INSTAGRAM_USERNAME

# ─────────────────────────────────────────────
#  إعداد التسجيل
# ─────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  إعداد Gemini
# ─────────────────────────────────────────────
genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel("gemini-2.0-flash")

# ─────────────────────────────────────────────
#  الثوابت
# ─────────────────────────────────────────────
FILES_PATH = Path(FILES_FOLDER)
DATA_FILE  = Path("user_data.json")

# حالات المحادثة
WAITING_SCREENSHOT = 1

# ─────────────────────────────────────────────
#  إدارة بيانات المستخدمين
# ─────────────────────────────────────────────
def load_data() -> dict:
    if DATA_FILE.exists():
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_data(data: dict) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_user(data: dict, user_id: int) -> dict:
    uid = str(user_id)
    if uid not in data:
        data[uid] = {
            "blocked_until":    None,
            "last_file":        None,
            "total_files":      0,
            "instagram_verified": False,
        }
    return data[uid]

def is_blocked(user_rec: dict) -> tuple[bool, int]:
    if user_rec["blocked_until"] is None:
        return False, 0
    blocked_until = datetime.fromisoformat(user_rec["blocked_until"])
    now = datetime.now()
    if now < blocked_until:
        remaining = int((blocked_until - now).total_seconds())
        return True, remaining
    return False, 0

# ─────────────────────────────────────────────
#  Gemini — تحليل صورة الإثبات
# ─────────────────────────────────────────────
async def verify_instagram_screenshot(image_bytes: bytes, username: str) -> tuple[bool, str]:
    """
    يتحقق من صورة إنستغرام باستخدام Gemini.
    في حالة فشل API يتم قبول الصورة تلقائياً إذا كان حجمها معقولاً.
    """
    try:
        import PIL.Image
        image = PIL.Image.open(BytesIO(image_bytes))
        width, height = image.size

        # صورة صغيرة جداً أو فارغة → رفض
        if width < 100 or height < 100:
            return False, "الصورة صغيرة جداً، يرجى إرسال صورة واضحة."

        prompt = f"""Look at this image carefully.

Does this image show proof that someone follows the Instagram account "{username}"?

Look for ANY of these signs:
- "Following" button or checkmark next to "{username}"
- The profile page of "{username}" with Following/Message button
- Any Instagram interface showing "{username}" account
- The word "Message" next to the account (means already following)

IMPORTANT: Be very lenient. If the image shows ANY Instagram profile page, answer VERIFIED.
Only answer NOT_VERIFIED if the image has absolutely nothing to do with Instagram.

Answer ONLY with: VERIFIED or NOT_VERIFIED on the first line.
Second line: brief reason in Arabic."""

        response = gemini_model.generate_content([prompt, image])
        text = response.text.strip()
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        result_line = lines[0].upper() if lines else ""
        reason = lines[1] if len(lines) > 1 else "تم التحليل بنجاح"

        logger.info(f"Gemini response: {text[:200]}")

        if "VERIFIED" in result_line and "NOT_VERIFIED" not in result_line:
            return True, reason
        else:
            return False, reason

    except Exception as e:
        error_str = str(e)
        logger.error(f"Gemini error: {error_str[:200]}")

        # في حالة تجاوز الحصة أو أي خطأ API → قبول الصورة تلقائياً
        if "429" in error_str or "quota" in error_str.lower() or "404" in error_str:
            logger.info("Quota exceeded - auto-accepting screenshot")
            return True, "تم قبول صورتك بنجاح ✅"
        
        return False, "حدث خطأ في التحليل. أرسل الصورة مرة أخرى."

# ─────────────────────────────────────────────
#  دوال مساعدة
# ─────────────────────────────────────────────
def get_available_files() -> list[Path]:
    if not FILES_PATH.exists():
        FILES_PATH.mkdir(parents=True, exist_ok=True)
        return []
    return [f for f in FILES_PATH.iterdir() if f.is_file()]

def format_time(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    parts = []
    if h: parts.append(f"{h} ساعة")
    if m: parts.append(f"{m} دقيقة")
    if s and not h: parts.append(f"{s} ثانية")
    return " و ".join(parts) if parts else "أقل من دقيقة"

def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ توليد ملف جديد", callback_data="generate")],
        [InlineKeyboardButton("📊 حالتي", callback_data="status")],
    ])

def verify_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📸 إرسال صورة الإثبات", callback_data="send_proof")],
        [InlineKeyboardButton(f"➡️ زيارة @{INSTAGRAM_USERNAME}", url=f"https://instagram.com/{INSTAGRAM_USERNAME}")],
    ])

def work_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ نعم، يعمل!", callback_data="works_yes"),
            InlineKeyboardButton("❌ لا يعمل",   callback_data="works_no"),
        ]
    ])

# ─────────────────────────────────────────────
#  معالجات الأوامر
# ─────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    data = load_data()
    rec  = get_user(data, user.id)
    save_data(data)

    verified = rec.get("instagram_verified", False)
    status_icon = "✅" if verified else "❌"
    status_text = "تم التحقق" if verified else "لم يتم التحقق بعد"

    welcome = (
        f"👋 أهلاً <b>{user.first_name}</b>!\n\n"
        f"🤖 <b>بوت توزيع الملفات الذكي</b>\n\n"
        f"📌 <b>كيف أعمل؟</b>\n"
        f"1️⃣ تابع حساب <b>@{INSTAGRAM_USERNAME}</b> على إنستغرام\n"
        f"2️⃣ أرسل صورة إثبات المتابعة\n"
        f"3️⃣ احصل على ملف عشوائي مجاناً!\n\n"
        f"📊 حالة الإنستغرام: {status_icon} <b>{status_text}</b>\n\n"
        f"{'🚀 يمكنك طلب ملف الآن!' if verified else '⬇️ ابدأ بالتحقق من متابعتك أولاً.'}"
    )
    await update.message.reply_html(welcome, reply_markup=main_keyboard())

async def cmd_verify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _ask_for_proof(update.message, context)

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = load_data()
    rec  = get_user(data, update.effective_user.id)
    blocked, remaining = is_blocked(rec)
    verified = rec.get("instagram_verified", False)

    status_block = (
        f"⏳ محظور — متبقي: <b>{format_time(remaining)}</b>" if blocked
        else "✅ جاهز للطلب"
    )
    ig_status = "✅ تم التحقق" if verified else "❌ لم يتم التحقق"

    text = (
        f"📊 <b>حالتك الحالية</b>\n\n"
        f"📱 إنستغرام: {ig_status}\n"
        f"📁 حالة الملفات: {status_block}\n"
        f"📦 إجمالي ملفاتك: <b>{rec.get('total_files', 0)}</b>\n"
        f"📄 آخر ملف: <code>{rec.get('last_file', '—')}</code>"
    )
    await update.message.reply_html(text, reply_markup=main_keyboard())

# ─────────────────────────────────────────────
#  طلب الإثبات
# ─────────────────────────────────────────────
async def _ask_for_proof(msg, context: ContextTypes.DEFAULT_TYPE) -> int:
    await msg.reply_html(
        f"📸 <b>التحقق من متابعة إنستغرام</b>\n\n"
        f"لاستلام ملفاتك، يجب أن تتابع:\n"
        f"👤 <b>@{INSTAGRAM_USERNAME}</b>\n\n"
        f"<b>خطوات الإثبات:</b>\n"
        f"1️⃣ افتح إنستغرام وابحث عن <code>{INSTAGRAM_USERNAME}</code>\n"
        f"2️⃣ اضغط <b>Follow / متابعة</b>\n"
        f"3️⃣ التقط <b>Screenshot</b> يظهر فيه زر <b>Following / متابَع</b>\n"
        f"4️⃣ أرسل الصورة هنا مباشرةً 👇",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(f"📲 زيارة @{INSTAGRAM_USERNAME}", url=f"https://instagram.com/{INSTAGRAM_USERNAME}")],
            [InlineKeyboardButton("❌ إلغاء", callback_data="back_home")],
        ])
    )
    return WAITING_SCREENSHOT

# ─────────────────────────────────────────────
#  استقبال الصورة وتحليلها بـ Gemini
# ─────────────────────────────────────────────
async def handle_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user    = update.effective_user
    message = update.message

    # التحقق أن الرسالة تحتوي صورة
    if not message.photo and not message.document:
        await message.reply_html(
            "⚠️ <b>يرجى إرسال صورة Screenshot فقط!</b>\n"
            "التقط صورة للشاشة وأرسلها هنا."
        )
        return WAITING_SCREENSHOT

    processing_msg = await message.reply_html(
        "🔍 <b>جاري تحليل الصورة بالذكاء الاصطناعي...</b>\n"
        "⏳ لحظة من فضلك..."
    )

    try:
        # تحميل الصورة
        if message.photo:
            photo_file = await message.photo[-1].get_file()
        else:
            photo_file = await message.document.get_file()

        image_bytes = await photo_file.download_as_bytearray()

        # تحليل Gemini
        is_verified, reason = await verify_instagram_screenshot(bytes(image_bytes), INSTAGRAM_USERNAME)

        if is_verified:
            # ✅ تم التحقق
            data = load_data()
            rec  = get_user(data, user.id)
            rec["instagram_verified"] = True
            save_data(data)

            await processing_msg.edit_text(
                f"✅ <b>تم التحقق بنجاح!</b>\n\n"
                f"🤖 <b>تحليل Gemini:</b> {reason}\n\n"
                f"🎉 أهلاً بك! يمكنك الآن طلب ملفاتك.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⚡ توليد ملف الآن!", callback_data="generate")]
                ])
            )
            return ConversationHandler.END

        else:
            # ❌ لم يتم التحقق
            await processing_msg.edit_text(
                f"❌ <b>لم يتم التحقق!</b>\n\n"
                f"🤖 <b>تحليل Gemini:</b> {reason}\n\n"
                f"📸 <b>تأكد من:</b>\n"
                f"• أن الصورة تظهر زر <b>Following / متابَع</b>\n"
                f"• أن اسم الحساب <b>@{INSTAGRAM_USERNAME}</b> ظاهر\n"
                f"• أن الصورة واضحة وغير مقصوصة\n\n"
                f"أرسل صورة أخرى للمحاولة مجدداً 👇",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(f"📲 زيارة @{INSTAGRAM_USERNAME}", url=f"https://instagram.com/{INSTAGRAM_USERNAME}")],
                    [InlineKeyboardButton("❌ إلغاء", callback_data="back_home")],
                ])
            )
            return WAITING_SCREENSHOT

    except Exception as e:
        logger.error(f"Screenshot handling error: {e}")
        await processing_msg.edit_text(
            "❌ <b>حدث خطأ!</b> يرجى إرسال الصورة مرة أخرى.",
            parse_mode="HTML"
        )
        return WAITING_SCREENSHOT

async def cancel_verify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_html("❌ تم إلغاء التحقق.", reply_markup=main_keyboard())
    return ConversationHandler.END

# ─────────────────────────────────────────────
#  معالجات الأزرار
# ─────────────────────────────────────────────
async def cb_generate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = load_data()
    rec  = get_user(data, query.from_user.id)

    # التحقق من إنستغرام أولاً
    if not rec.get("instagram_verified", False):
        await query.edit_message_text(
            f"🔒 <b>يجب التحقق من إنستغرام أولاً!</b>\n\n"
            f"تابع <b>@{INSTAGRAM_USERNAME}</b> ثم أرسل صورة إثبات.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📸 التحقق الآن", callback_data="verify_ig")],
                [InlineKeyboardButton("🔙 رجوع",       callback_data="back_home")],
            ])
        )
        return

    # التحقق من الحظر
    blocked, remaining = is_blocked(rec)
    if blocked:
        await query.edit_message_text(
            f"⏰ <b>يجب الانتظار!</b>\n\n"
            f"⏳ الوقت المتبقي: <b>{format_time(remaining)}</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 رجوع", callback_data="back_home")]
            ])
        )
        return

    # التحقق من وجود ملفات
    files = get_available_files()
    if not files:
        await query.edit_message_text(
            "⚠️ <b>لا توجد ملفات متاحة حالياً!</b>\n"
            "تواصل مع المسؤول.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 رجوع", callback_data="back_home")]
            ])
        )
        return

    # إرسال ملف عشوائي
    chosen = random.choice(files)
    logger.info(f"User {query.from_user.id} → {chosen.name}")

    await query.edit_message_text("📤 <b>جاري تجهيز ملفك...</b>", parse_mode="HTML")

    try:
        with open(chosen, "rb") as f:
            await query.message.reply_document(
                document=f,
                filename=chosen.name,
                caption=(
                    f"🎁 <b>ملفك الجديد!</b>\n\n"
                    f"📄 <code>{chosen.name}</code>\n"
                    f"📦 الحجم: <b>{chosen.stat().st_size // 1024} KB</b>\n\n"
                    f"هل الملف يعمل؟"
                ),
                parse_mode="HTML",
                reply_markup=work_keyboard(),
            )

        chosen.unlink()
        logger.info(f"Deleted: {chosen.name}")

        rec["last_file"]   = chosen.name
        rec["total_files"] = rec.get("total_files", 0) + 1
        save_data(data)

        await query.edit_message_text(
            "✅ <b>تم إرسال الملف!</b>\n\nأخبرني هل يعمل أم لا 👆",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 الرئيسية", callback_data="back_home")]
            ])
        )

    except Exception as e:
        logger.error(f"Send error: {e}")
        await query.edit_message_text(
            "❌ <b>خطأ في الإرسال!</b> حاول مجدداً.",
            parse_mode="HTML",
            reply_markup=main_keyboard()
        )


async def cb_verify_ig(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        f"📸 <b>التحقق من إنستغرام</b>\n\n"
        f"1️⃣ تابع <b>@{INSTAGRAM_USERNAME}</b> على إنستغرام\n"
        f"2️⃣ التقط Screenshot يظهر زر <b>Following / متابَع</b>\n"
        f"3️⃣ أرسل الصورة هنا الآن 👇",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(f"📲 زيارة @{INSTAGRAM_USERNAME}", url=f"https://instagram.com/{INSTAGRAM_USERNAME}")],
            [InlineKeyboardButton("❌ إلغاء", callback_data="back_home")],
        ])
    )
    context.user_data["waiting_ig_proof"] = True


async def cb_works_yes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("✅ رائع! سنراك بعد 24 ساعة.")

    data = load_data()
    rec  = get_user(data, query.from_user.id)
    blocked_until = datetime.now() + timedelta(hours=COOLDOWN_HOURS)
    rec["blocked_until"] = blocked_until.isoformat()
    save_data(data)

    until_str = blocked_until.strftime("%Y-%m-%d %H:%M")
    await query.edit_message_caption(
        caption=(
            f"🎉 <b>ممتاز! يسعدنا أن الملف يعمل.</b>\n\n"
            f"⏰ ملف جديد متاح بعد <b>{COOLDOWN_HOURS} ساعة</b>\n"
            f"📅 في: <b>{until_str}</b>\n\n"
            "نراك قريباً! 👋"
        ),
        parse_mode="HTML",
    )


async def cb_works_no(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("🔄 سأرسل ملفاً آخر فوراً!")

    await query.edit_message_caption(
        caption="❌ <b>نأسف!</b> جاري إرسال ملف بديل...",
        parse_mode="HTML",
    )

    data  = load_data()
    rec   = get_user(data, query.from_user.id)
    files = get_available_files()

    if not files:
        await query.message.reply_html(
            "⚠️ <b>لا توجد ملفات متاحة!</b>",
            reply_markup=main_keyboard()
        )
        return

    chosen = random.choice(files)
    try:
        with open(chosen, "rb") as f:
            await query.message.reply_document(
                document=f,
                filename=chosen.name,
                caption=(
                    f"🔄 <b>ملف بديل!</b>\n\n"
                    f"📄 <code>{chosen.name}</code>\n"
                    f"📦 الحجم: <b>{chosen.stat().st_size // 1024} KB</b>\n\n"
                    "هل هذا يعمل؟"
                ),
                parse_mode="HTML",
                reply_markup=work_keyboard(),
            )
        chosen.unlink()
        rec["last_file"]   = chosen.name
        rec["total_files"] = rec.get("total_files", 0) + 1
        save_data(data)
    except Exception as e:
        logger.error(f"Replacement send error: {e}")
        await query.message.reply_html("❌ خطأ في الإرسال!", reply_markup=main_keyboard())


async def cb_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data    = load_data()
    rec     = get_user(data, query.from_user.id)
    blocked, remaining = is_blocked(rec)
    verified = rec.get("instagram_verified", False)

    status_block = (
        f"⏳ محظور — متبقي: <b>{format_time(remaining)}</b>" if blocked
        else "✅ جاهز للطلب"
    )
    ig_status = "✅ تم التحقق" if verified else "❌ لم يتم التحقق"

    await query.edit_message_text(
        f"📊 <b>حالتك</b>\n\n"
        f"📱 إنستغرام: {ig_status}\n"
        f"📁 الملفات: {status_block}\n"
        f"📦 إجمالي: <b>{rec.get('total_files', 0)}</b>\n"
        f"📄 آخر ملف: <code>{rec.get('last_file', '—')}</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 رجوع", callback_data="back_home")]
        ])
    )


async def cb_back_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    context.user_data.pop("waiting_ig_proof", None)
    await query.edit_message_text(
        f"🏠 <b>الصفحة الرئيسية</b>\n\nأهلاً <b>{query.from_user.first_name}</b>!",
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )


# ─────────────────────────────────────────────
#  معالج الصور العام (خارج ConversationHandler)
# ─────────────────────────────────────────────
async def handle_photo_global(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """يستقبل الصور في أي وقت ويتحقق منها"""
    user    = update.effective_user
    message = update.message

    # تحقق هل المستخدم في وضع انتظار الإثبات
    data = load_data()
    rec  = get_user(data, user.id)

    if rec.get("instagram_verified", False):
        await message.reply_html(
            "✅ <b>حسابك محقق مسبقاً!</b>\n"
            "يمكنك توليد ملف الآن.",
            reply_markup=main_keyboard()
        )
        return

    processing_msg = await message.reply_html(
        "🔍 <b>جاري تحليل الصورة بـ Gemini AI...</b>\n⏳ لحظة..."
    )

    try:
        if message.photo:
            photo_file = await message.photo[-1].get_file()
        else:
            photo_file = await message.document.get_file()

        image_bytes = await photo_file.download_as_bytearray()
        is_verified, reason = await verify_instagram_screenshot(bytes(image_bytes), INSTAGRAM_USERNAME)

        if is_verified:
            rec["instagram_verified"] = True
            save_data(data)
            await processing_msg.edit_text(
                f"✅ <b>تم التحقق بنجاح!</b>\n\n"
                f"🤖 Gemini: {reason}\n\n"
                f"🎉 يمكنك الآن طلب ملفاتك!",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⚡ توليد ملف الآن!", callback_data="generate")]
                ])
            )
        else:
            save_data(data)
            await processing_msg.edit_text(
                f"❌ <b>لم يتم التحقق!</b>\n\n"
                f"🤖 Gemini: {reason}\n\n"
                f"• تأكد أن الصورة تُظهر زر <b>Following</b>\n"
                f"• تأكد أن اسم <b>@{INSTAGRAM_USERNAME}</b> ظاهر\n\n"
                f"أرسل صورة أخرى 👇",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(f"📲 @{INSTAGRAM_USERNAME}", url=f"https://instagram.com/{INSTAGRAM_USERNAME}")]
                ])
            )
    except Exception as e:
        logger.error(f"Photo handler error: {e}")
        await processing_msg.edit_text("❌ خطأ في التحليل. أرسل الصورة مرة أخرى.")


# ─────────────────────────────────────────────
#  نقطة الدخول
# ─────────────────────────────────────────────
def main() -> None:
    logger.info("🚀 تشغيل البوت...")

    app = Application.builder().token(BOT_TOKEN).build()

    # أوامر
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("help",   cmd_start))
    app.add_handler(CommandHandler("verify", cmd_verify))
    app.add_handler(CommandHandler("status", cmd_status))

    # أزرار
    app.add_handler(CallbackQueryHandler(cb_generate,  pattern="^generate$"))
    app.add_handler(CallbackQueryHandler(cb_verify_ig, pattern="^verify_ig$"))
    app.add_handler(CallbackQueryHandler(cb_works_yes, pattern="^works_yes$"))
    app.add_handler(CallbackQueryHandler(cb_works_no,  pattern="^works_no$"))
    app.add_handler(CallbackQueryHandler(cb_status,    pattern="^status$"))
    app.add_handler(CallbackQueryHandler(cb_back_home, pattern="^back_home$"))

    # صور (للتحقق من إنستغرام)
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_photo_global))

    logger.info("✅ البوت يعمل! اضغط Ctrl+C للإيقاف.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
