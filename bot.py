import os
import threading
from flask import Flask
from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext

# ================== إعدادات ==================
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID"))

user_data = {}

# ================== Keep Alive ==================
app = Flask('')

@app.route('/')
def home():
    return "Bot is running!"

def run_web():
    app.run(host='0.0.0.0', port=10000)

def keep_alive():
    t = threading.Thread(target=run_web)
    t.start()

# ================== الأزرار ==================
main_keyboard = ReplyKeyboardMarkup([
    ["📥 طلب خدمة"],
    ["📚 خدماتنا", "📞 تواصل"]
], resize_keyboard=True)

service_keyboard = ReplyKeyboardMarkup([
    ["📊 عرض بوربوينت"],
    ["📄 ورقة عمل"],
    ["📝 اختبار"],
    ["🔙 رجوع"]
], resize_keyboard=True)

# ================== الأوامر ==================
def start(update: Update, context: CallbackContext):
    update.message.reply_text(
        "👋 أهلاً بك في بوت خدمات الصوفي\nاختر من القائمة:",
        reply_markup=main_keyboard
    )

def handle(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    text = update.message.text

    if text == "📥 طلب خدمة":
        user_data[user_id] = {"step": "service"}
        update.message.reply_text("اختر نوع الخدمة 👇", reply_markup=service_keyboard)

    elif user_id in user_data and user_data[user_id]["step"] == "service":
        if text == "🔙 رجوع":
            update.message.reply_text("رجعت للقائمة الرئيسية", reply_markup=main_keyboard)
            del user_data[user_id]
            return

        user_data[user_id]["service"] = text
        user_data[user_id]["step"] = "name"
        update.message.reply_text("ما اسمك؟")

    elif user_id in user_data and user_data[user_id]["step"] == "name":
        user_data[user_id]["name"] = text
        user_data[user_id]["step"] = "details"
        update.message.reply_text("اكتب تفاصيل الطلب:")

    elif user_id in user_data and user_data[user_id]["step"] == "details":
        user_data[user_id]["details"] = text
        user_data[user_id]["step"] = "time"
        update.message.reply_text("متى موعد التسليم؟")

    elif user_id in user_data and user_data[user_id]["step"] == "time":
        user_data[user_id]["time"] = text

        data = user_data[user_id]

        msg = f"""
📥 طلب جديد:

👤 الاسم: {data['name']}
📌 الخدمة: {data['service']}
📝 التفاصيل: {data['details']}
⏱ الموعد: {data['time']}
"""

        context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=msg)

        update.message.reply_text("✅ تم استلام طلبك", reply_markup=main_keyboard)

        del user_data[user_id]

    elif text == "📚 خدماتنا":
        update.message.reply_text(
            "📚 خدماتنا:\n\n📊 عروض بوربوينت\n📄 أوراق عمل\n📝 اختبارات",
            reply_markup=main_keyboard
        )

    elif text == "📞 تواصل":
        update.message.reply_text(
            "📞 تواصل معنا:\n@YOUR_USERNAME",
            reply_markup=main_keyboard
        )

# ================== التشغيل ==================
def main():
    keep_alive()  # تشغيل السيرفر

    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle))

    print("Bot running...")
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
