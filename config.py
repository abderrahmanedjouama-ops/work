import os

# ─────────────────────────────────────────────────────────────────
#  الإعدادات — تُقرأ من متغيرات البيئة (Railway) أو القيم الافتراضية
# ─────────────────────────────────────────────────────────────────

# 🔑 توكن البوت
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8543526090:AAG0meq-DscWar5NxXpGpXTWJGqGQ0SECCw")

# 📁 مسار مجلد الملفات (على Railway يكون نسبياً)
FILES_FOLDER = os.environ.get("FILES_FOLDER", "files")

# ⏰ ساعات الانتظار بعد نجاح الملف
COOLDOWN_HOURS = int(os.environ.get("COOLDOWN_HOURS", "24"))

# 🤖 Gemini API Key
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AIzaSyCRPmNznbWBPQfj0J-xH2f38_fN9VSDZsg")

# 📸 اسم حساب إنستغرام (بدون @)
INSTAGRAM_USERNAME = os.environ.get("INSTAGRAM_USERNAME", "eng_abderrahmane")
