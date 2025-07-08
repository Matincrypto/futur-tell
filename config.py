# config.py

import time
from datetime import datetime

# --- Telegram Bot Configuration ---
# توکن ربات تلگرام خود را اینجا وارد کنید
TELEGRAM_BOT_TOKEN = "7435237309:AAEAXXkce1VU8Wk-NqxX1v6VKnSMaydbErs"
# شناسه چت گروه یا کانال
TELEGRAM_CHAT_ID = -1002684336789
# شناسه تاپیک (Topic) مورد نظر در گروه (اگر گروه شما تاپیک‌بندی شده است)
TELEGRAM_MESSAGE_THREAD_ID = 1126
# --- End Telegram Bot Configuration ---


# --- Indicator Settings ---
# مقدار کلیدی برای محاسبه اندیکاتور
KEY_VALUE = 1
# دوره زمانی برای محاسبه ATR
ATR_PERIOD = 10
# استفاده از کندل‌های هیکن آشی (True) یا کندل‌های عادی (False)
USE_HEIKIN_ASHI = False
# --- End Indicator Settings ---


# --- Analysis Settings ---
# تایم‌فریم مورد نظر برای تحلیل (به دقیقه)
# مثال: "60" برای یک ساعته, "240" برای چهار ساعته, "D" برای روزانه
RESOLUTION_TO_USE = "60"
# تعداد کندل‌های اخیر که برای یافتن سیگنال بررسی می‌شوند
RECENT_CANDLES_TO_CHECK = 10
# تعداد روزهای گذشته که دیتا برای تحلیل از آن‌ها دریافت می‌شود
DAYS_OF_DATA_TO_FETCH = 30
# --- End Analysis Settings ---


# --- Output Settings ---
# نام پوشه‌ای که فایل اکسل خروجی در آن ذخیره می‌شود
OUTPUT_DIRECTORY = "wallex_analysis_results"
# --- End Output Settings ---


# --- Time Calculation (Do not change unless you know what you are doing) ---
END_TIME = int(time.time())
START_TIME = END_TIME - (DAYS_OF_DATA_TO_FETCH * 24 * 60 * 60)