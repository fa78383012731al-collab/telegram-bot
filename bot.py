import os
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# قراءة المتغيرات من Render
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

user_data = {}

# الأزرار
keyboard = [
    ["📥 طلب خدمة"],
    ["📚 خدماتنا", "📞 تواصل"]
]

reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# أمر البداية
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أهلاً بك في بوت خدمات الصوفي 👋\nاختر من القائمة:",
        reply_markup=reply_markup
    )

# التعامل مع الرسائل
async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    text = update.message.text

    # بدء الطلب
    if text == "📥 طلب خدمة":
        user_data[user_id] = {"step": "name"}
        await update.message.reply_text("ما اسمك؟")

    # خطوات الطلب
    elif user_id in user_data:
        step = user_data[user_id]["step"]

        if step == "name":
            user_data[user_id]["name"] = text
            user_data[user_id]["step"] = "service"
            await update.message.reply_text("ما نوع الخدمة؟")

        elif step == "service":
            user_data[user_id]["service"] = text
            user_data[user_id]["step"] = "details"
            await update.message.reply_text("اكتب التفاصيل:")

        elif step == "details":
            user_data[user_id]["details"] = text
            user_data[user_id]["step"] = "time"
            await update.message.reply_text("متى موعد التسليم؟")

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

            # إرسال الطلب لك
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=msg)

            await update.message.reply_text("تم استلام طلبك ✅")
            del user_data[user_id]

    # خدماتنا
    elif text == "📚 خدماتنا":
        await update.message.reply_text(
            "📚 خدماتنا:\n- عروض بوربوينت\n- أوراق عمل\n- اختبارات"
        )

    # تواصل
    elif text == "📞 تواصل":
        await update.message.reply_text("📞 تواصل: @YOUR_USERNAME")


# ✅ هنا التصحيح المهم (بدون async)
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))

    print("Bot running...")
    app.run_polling()


if __name__ == "__main__":
    main()
