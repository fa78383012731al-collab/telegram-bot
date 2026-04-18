import os
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID"))

user_data = {}

keyboard = [
    ["📥 طلب خدمة"],
    ["📚 خدماتنا", "📞 تواصل"]
]

reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def start(update: Update, context: CallbackContext):
    update.message.reply_text(
        "أهلاً بك في بوت خدمات الصوفي 👋\nاختر من القائمة:",
        reply_markup=reply_markup
    )

def handle(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    text = update.message.text

    if text == "📥 طلب خدمة":
        user_data[user_id] = {"step": "name"}
        update.message.reply_text("ما اسمك؟")

    elif user_id in user_data:
        step = user_data[user_id]["step"]

        if step == "name":
            user_data[user_id]["name"] = text
            user_data[user_id]["step"] = "service"
            update.message.reply_text("ما نوع الخدمة؟")

        elif step == "service":
            user_data[user_id]["service"] = text
            user_data[user_id]["step"] = "details"
            update.message.reply_text("اكتب التفاصيل:")

        elif step == "details":
            user_data[user_id]["details"] = text
            user_data[user_id]["step"] = "time"
            update.message.reply_text("متى موعد التسليم؟")

        elif step == "time":
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

            update.message.reply_text("تم استلام طلبك ✅")
            del user_data[user_id]

    elif text == "📚 خدماتنا":
        update.message.reply_text(
            "📚 خدماتنا:\n- عروض بوربوينت\n- أوراق عمل\n- اختبارات"
        )

    elif text == "📞 تواصل":
        update.message.reply_text("📞 تواصل: @YOUR_USERNAME")


def main():
    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle))

    print("Bot running...")
    updater.start_polling()
    updater.idle()


if __name__ == "__main__":
    main()
