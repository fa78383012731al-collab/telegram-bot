import os
import re
import sqlite3
import threading
import logging
from datetime import datetime

from flask import Flask
from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

# =========================
# ENV
# =========================
TOKEN = os.getenv("BOT_TOKEN")
NOTIFY_CHAT_ID_RAW = os.getenv("NOTIFY_CHAT_ID") or os.getenv("ADMIN_CHAT_ID")
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "")
DB_PATH = os.getenv("DB_PATH", "orders.db")

def to_int(value):
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None

NOTIFY_CHAT_ID = to_int(NOTIFY_CHAT_ID_RAW)

ADMIN_IDS = set()
for item in ADMIN_IDS_RAW.split(","):
    item = item.strip()
    if item and re.fullmatch(r"-?\d+", item):
        ADMIN_IDS.add(int(item))

# =========================
# CATEGORIES
# =========================
CATEGORIES = [
    "ملفات الأداء والإنجاز",
    "السجلات والتقارير",
    "الملفات الإدارية",
    "نافس",
    "الخطط",
    "التحليل والاختبارات",
    "التصاميم",
    "التنسيق والذكاء الاصطناعي",
    "الخدمات الجامعية",
    "أخرى",
]

STATUS_LABELS = {
    "new": "جديد",
    "in_progress": "جاري العمل",
    "waiting_review": "بانتظار المراجعة",
    "done": "تم الإنجاز",
    "cancelled": "ملغي",
}

# =========================
# KEYBOARDS
# =========================
MAIN_USER_KB = ReplyKeyboardMarkup(
    [
        ["➕ طلب جديد"],
        ["🔎 متابعة طلب"],
    ],
    resize_keyboard=True
)

MAIN_ADMIN_KB = ReplyKeyboardMarkup(
    [
        ["➕ طلب جديد"],
        ["🔎 متابعة طلب"],
        ["📋 قائمة الطلبات"],
    ],
    resize_keyboard=True
)

CATEGORY_KB = ReplyKeyboardMarkup(
    [
        ["ملفات الأداء والإنجاز", "السجلات والتقارير"],
        ["الملفات الإدارية", "نافس"],
        ["الخطط", "التحليل والاختبارات"],
        ["التصاميم", "التنسيق والذكاء الاصطناعي"],
        ["الخدمات الجامعية", "أخرى"],
        ["🔙 رجوع"],
    ],
    resize_keyboard=True
)

PRIORITY_KB = ReplyKeyboardMarkup(
    [
        ["عادي", "عاجل"],
        ["🔙 رجوع"],
    ],
    resize_keyboard=True
)

CONFIRM_KB = ReplyKeyboardMarkup(
    [
        ["✅ تأكيد", "❌ إلغاء"],
        ["🔙 رجوع"],
    ],
    resize_keyboard=True
)

# =========================
# FLASK KEEP ALIVE
# =========================
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running"

def run_web():
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    t = threading.Thread(target=run_web)
    t.start()

# =========================
# DATABASE
# =========================
def db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                requester_chat_id INTEGER NOT NULL,
                requester_user_id INTEGER NOT NULL,
                requester_name TEXT,
                requester_username TEXT,
                intermediary_name TEXT NOT NULL,
                final_client TEXT NOT NULL,
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                details TEXT NOT NULL,
                price REAL NOT NULL,
                deadline TEXT NOT NULL,
                priority TEXT NOT NULL,
                status TEXT NOT NULL,
                assignee TEXT,
                notes TEXT
            )
            """
        )
        conn.commit()

def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M")

def create_order(data):
    with db_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO orders (
                created_at, updated_at,
                requester_chat_id, requester_user_id, requester_name, requester_username,
                intermediary_name, final_client, category, title, details,
                price, deadline, priority, status, assignee, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now_text(), now_text(),
                data["requester_chat_id"], data["requester_user_id"], data.get("requester_name"), data.get("requester_username"),
                data["intermediary_name"], data["final_client"], data["category"], data["title"], data["details"],
                data["price"], data["deadline"], data["priority"], "new", data.get("assignee"), data.get("notes"),
            ),
        )
        conn.commit()
        return cur.lastrowid

def get_order(order_id):
    with db_conn() as conn:
        return conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()

def list_open_orders(limit=20):
    with db_conn() as conn:
        return conn.execute(
            """
            SELECT * FROM orders
            WHERE status IN ('new', 'in_progress', 'waiting_review')
            ORDER BY
                CASE priority WHEN 'عاجل' THEN 0 ELSE 1 END,
                COALESCE(deadline, '9999-12-31 23:59') ASC,
                id ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

def list_all_recent(limit=20):
    with db_conn() as conn:
        return conn.execute(
            """
            SELECT * FROM orders
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

def update_status(order_id, status, assignee=None, notes=None):
    with db_conn() as conn:
        conn.execute(
            """
            UPDATE orders
            SET status = ?, assignee = COALESCE(?, assignee), notes = COALESCE(?, notes), updated_at = ?
            WHERE id = ?
            """,
            (status, assignee, notes, now_text(), order_id),
        )
        conn.commit()

# =========================
# HELPERS
# =========================
def is_admin(user_id):
    return user_id in ADMIN_IDS

def main_keyboard(user_id):
    return MAIN_ADMIN_KB if is_admin(user_id) else MAIN_USER_KB

def reset_flow(context: CallbackContext):
    context.user_data.pop("workflow", None)

def start_flow(context: CallbackContext):
    context.user_data["workflow"] = {
        "step": "intermediary_name",
        "data": {},
    }

def current_flow(context: CallbackContext):
    return context.user_data.get("workflow")

def parse_price(text):
    cleaned = text.strip().replace("ريال", "").replace("SAR", "").replace(",", ".")
    cleaned = re.sub(r"[^\d.]", "", cleaned)
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None

def order_summary(data):
    return (
        f"📋 ملخص الطلب:\n\n"
        f"👤 الوسيط: {data['intermediary_name']}\n"
        f"🏷 العميل النهائي: {data['final_client']}\n"
        f"🗂 الفئة: {data['category']}\n"
        f"📌 عنوان/اسم الملف: {data['title']}\n"
        f"📝 التفاصيل: {data['details']}\n"
        f"💰 السعر: {data['price']} ريال\n"
        f"⏱ الموعد: {data['deadline']}\n"
        f"⚡ الأولوية: {data['priority']}\n"
    )

def order_full_text(order):
    return (
        f"🧾 رقم الطلب: #{order['id']}\n"
        f"📅 وقت الإنشاء: {order['created_at']}\n"
        f"🕒 آخر تحديث: {order['updated_at']}\n\n"
        f"👤 الوسيط: {order['intermediary_name']}\n"
        f"🏷 العميل النهائي: {order['final_client']}\n"
        f"🗂 الفئة: {order['category']}\n"
        f"📌 العنوان: {order['title']}\n"
        f"📝 التفاصيل: {order['details']}\n"
        f"💰 السعر: {order['price']} ريال\n"
        f"⏱ الموعد: {order['deadline']}\n"
        f"⚡ الأولوية: {order['priority']}\n"
        f"📌 الحالة: {STATUS_LABELS.get(order['status'], order['status'])}\n"
        f"👨‍💻 المسند إليه: {order['assignee'] or 'غير محدد'}\n"
        f"🗒 ملاحظات: {order['notes'] or '-'}\n"
    )

def notify_team(context: CallbackContext, text: str):
    if NOTIFY_CHAT_ID:
        try:
            context.bot.send_message(chat_id=NOTIFY_CHAT_ID, text=text)
        except Exception as e:
            logging.warning(f"Team notify failed: {e}")

def notify_requester(context: CallbackContext, chat_id: int, text: str):
    try:
        context.bot.send_message(chat_id=chat_id, text=text)
    except Exception as e:
        logging.warning(f"Requester notify failed: {e}")

def show_queue(update: Update, context: CallbackContext):
    if not is_admin(update.effective_user.id):
        update.message.reply_text("هذا الأمر مخصص للإدارة فقط.", reply_markup=main_keyboard(update.effective_user.id))
        return

    orders = list_open_orders()
    if not orders:
        update.message.reply_text("لا توجد طلبات مفتوحة حالياً.", reply_markup=main_keyboard(update.effective_user.id))
        return

    lines = ["📋 قائمة الطلبات المفتوحة:\n"]
    for o in orders:
        lines.append(
            f"#{o['id']} | {STATUS_LABELS.get(o['status'], o['status'])} | {o['priority']} | {o['deadline']} | {o['category']} | {o['title']}"
        )
    update.message.reply_text("\n".join(lines), reply_markup=main_keyboard(update.effective_user.id))

# =========================
# COMMANDS
# =========================
def cmd_start(update: Update, context: CallbackContext):
    reset_flow(context)
    user_id = update.effective_user.id
    text = (
        "أهلاً بك في نظام إدارة الطلبات 👋\n\n"
        "النظام مخصص لفريقكم ويعمل بهذا التسلسل:\n"
        "• استقبال من الوسيط\n"
        "• تسجيل السعر والموعد\n"
        "• ترتيب تلقائي حسب الأولوية\n"
        "• متابعة حتى الإنجاز\n\n"
        "اختر من القائمة:"
    )
    update.message.reply_text(text, reply_markup=main_keyboard(user_id))

def cmd_help(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    text = (
        "الأوامر الأساسية:\n"
        "/start - القائمة الرئيسية\n"
        "/cancel - إلغاء العملية الحالية\n"
        "/queue - قائمة الطلبات المفتوحة (للإدارة)\n"
        "/order <id> - تفاصيل طلب\n"
        "/progress <id> - جعل الطلب جاري العمل\n"
        "/review <id> - بانتظار المراجعة\n"
        "/done <id> - تم الإنجاز\n"
        "/cancelorder <id> - إلغاء الطلب\n"
        "/assign <id> <اسم> - إسناد الطلب\n"
        "/id - إظهار رقم المحادثة أو المجموعة\n"
    )
    update.message.reply_text(text, reply_markup=main_keyboard(user_id))

def cmd_cancel(update: Update, context: CallbackContext):
    reset_flow(context)
    update.message.reply_text("تم إلغاء العملية الحالية.", reply_markup=main_keyboard(update.effective_user.id))

def cmd_queue(update: Update, context: CallbackContext):
    show_queue(update, context)

def cmd_order(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        update.message.reply_text("هذا الأمر مخصص للإدارة فقط.", reply_markup=main_keyboard(user_id))
        return

    if not context.args:
        update.message.reply_text("استخدم: /order 12", reply_markup=main_keyboard(user_id))
        return

    if not context.args[0].isdigit():
        update.message.reply_text("رقم الطلب يجب أن يكون رقمًا.", reply_markup=main_keyboard(user_id))
        return

    order = get_order(int(context.args[0]))
    if not order:
        update.message.reply_text("الطلب غير موجود.", reply_markup=main_keyboard(user_id))
        return

    update.message.reply_text(order_full_text(order), reply_markup=main_keyboard(user_id))

def set_status_command(update: Update, context: CallbackContext, status: str):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        update.message.reply_text("هذا الأمر مخصص للإدارة فقط.", reply_markup=main_keyboard(user_id))
        return

    if not context.args:
        update.message.reply_text("استخدم: /command <id>", reply_markup=main_keyboard(user_id))
        return

    if not context.args[0].isdigit():
        update.message.reply_text("رقم الطلب يجب أن يكون رقمًا.", reply_markup=main_keyboard(user_id))
        return

    order_id = int(context.args[0])
    order = get_order(order_id)
    if not order:
        update.message.reply_text("الطلب غير موجود.", reply_markup=main_keyboard(user_id))
        return

    update_status(order_id, status)
    update.message.reply_text(f"تم تحديث الطلب #{order_id} إلى: {STATUS_LABELS[status]}", reply_markup=main_keyboard(user_id))
    notify_requester(
        context,
        order["requester_chat_id"],
        f"تم تحديث طلبك رقم #{order_id} إلى: {STATUS_LABELS[status]}"
    )

def cmd_progress(update: Update, context: CallbackContext):
    set_status_command(update, context, "in_progress")

def cmd_review(update: Update, context: CallbackContext):
    set_status_command(update, context, "waiting_review")

def cmd_done(update: Update, context: CallbackContext):
    set_status_command(update, context, "done")

def cmd_cancelorder(update: Update, context: CallbackContext):
    set_status_command(update, context, "cancelled")

def cmd_assign(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        update.message.reply_text("هذا الأمر مخصص للإدارة فقط.", reply_markup=main_keyboard(user_id))
        return

    if len(context.args) < 2:
        update.message.reply_text("استخدم: /assign 12 فيصل", reply_markup=main_keyboard(user_id))
        return

    if not context.args[0].isdigit():
        update.message.reply_text("رقم الطلب يجب أن يكون رقمًا.", reply_markup=main_keyboard(user_id))
        return

    order_id = int(context.args[0])
    assignee = " ".join(context.args[1:]).strip()
    order = get_order(order_id)
    if not order:
        update.message.reply_text("الطلب غير موجود.", reply_markup=main_keyboard(user_id))
        return

    update_status(order_id, order["status"], assignee=assignee)
    update.message.reply_text(f"تم إسناد الطلب #{order_id} إلى: {assignee}", reply_markup=main_keyboard(user_id))
    notify_requester(
        context,
        order["requester_chat_id"],
        f"تم إسناد طلبك رقم #{order_id} إلى: {assignee}"
    )

def cmd_id(update: Update, context: CallbackContext):
    chat = update.effective_chat
    update.message.reply_text(
        f"رقم المحادثة الحالي:\n{chat.id}\n\n"
        f"إذا كانت هذه مجموعة الفريق، انسخ هذا الرقم وضعه في NOTIFY_CHAT_ID.",
        reply_markup=main_keyboard(update.effective_user.id)
    )

# =========================
# FLOW HANDLER
# =========================
def handle_text(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()

    if text == "🔙 رجوع":
        cmd_cancel(update, context)
        return

    if text == "📋 قائمة الطلبات":
        show_queue(update, context)
        return

    if text == "➕ طلب جديد":
        start_flow(context)
        context.user_data["workflow"]["data"] = {
            "requester_chat_id": chat_id,
            "requester_user_id": user_id,
            "requester_name": update.effective_user.full_name or "",
            "requester_username": update.effective_user.username or "",
        }
        update.message.reply_text("اكتب اسم الوسيط:", reply_markup=main_keyboard(user_id))
        return

    if text == "🔎 متابعة طلب":
        context.user_data["workflow"] = {
            "step": "track_order",
            "data": {},
        }
        update.message.reply_text("أرسل رقم الطلب فقط:", reply_markup=main_keyboard(user_id))
        return

    flow = current_flow(context)
    if not flow:
        update.message.reply_text("اختر من القائمة.", reply_markup=main_keyboard(user_id))
        return

    step = flow.get("step")
    data = flow.get("data", {})

    # متابعة طلب
    if step == "track_order":
        if not text.isdigit():
            update.message.reply_text("رقم الطلب يجب أن يكون رقمًا فقط.")
            return

        order = get_order(int(text))
        if not order:
            update.message.reply_text("الطلب غير موجود.")
            reset_flow(context)
            return

        update.message.reply_text(order_full_text(order), reply_markup=main_keyboard(user_id))
        reset_flow(context)
        return

    # اسم الوسيط
    if step == "intermediary_name":
        data["intermediary_name"] = text
        flow["step"] = "final_client"
        update.message.reply_text("اكتب اسم صاحب العمل النهائي / العميل:", reply_markup=main_keyboard(user_id))
        return

    # العميل النهائي
    if step == "final_client":
        data["final_client"] = text
        flow["step"] = "category"
        update.message.reply_text("اختر الفئة الرئيسية:", reply_markup=CATEGORY_KB)
        return

    # الفئة
    if step == "category":
        if text == "أخرى":
            flow["step"] = "custom_category"
            update.message.reply_text("اكتب اسم الفئة التي تريدها:", reply_markup=main_keyboard(user_id))
            return

        if text not in CATEGORIES:
            update.message.reply_text("اختر الفئة من الأزرار فقط.", reply_markup=CATEGORY_KB)
            return

        data["category"] = text
        flow["step"] = "title"
        update.message.reply_text("اكتب اسم الملف أو عنوان الطلب:", reply_markup=main_keyboard(user_id))
        return

    # فئة مخصصة
    if step == "custom_category":
        data["category"] = text
        flow["step"] = "title"
        update.message.reply_text("اكتب اسم الملف أو عنوان الطلب:", reply_markup=main_keyboard(user_id))
        return

    # العنوان
    if step == "title":
        data["title"] = text
        flow["step"] = "details"
        update.message.reply_text(
            "اكتب التفاصيل كاملة:\n"
            "مثال: الصف، الدرس، عدد الشرائح/الصفحات، النمط، أي ملاحظات مهمة.",
            reply_markup=main_keyboard(user_id)
        )
        return

    # التفاصيل
    if step == "details":
        data["details"] = text
        flow["step"] = "price"
        update.message.reply_text("اكتب السعر المتفق عليه بالأرقام فقط:", reply_markup=main_keyboard(user_id))
        return

    # السعر
    if step == "price":
        price = parse_price(text)
        if price is None:
            update.message.reply_text("اكتب السعر بالأرقام فقط، مثل: 25", reply_markup=main_keyboard(user_id))
            return

        data["price"] = price
        flow["step"] = "deadline"
        update.message.reply_text(
            "اكتب موعد التسليم بصيغة واضحة، مثل:\n2026-04-18 14:30",
            reply_markup=main_keyboard(user_id)
        )
        return

    # الموعد
    if step == "deadline":
        data["deadline"] = text
        flow["step"] = "priority"
        update.message.reply_text("اختر الأولوية:", reply_markup=PRIORITY_KB)
        return

    # الأولوية
    if step == "priority":
        if text not in ["عادي", "عاجل"]:
            update.message.reply_text("اختر الأولوية من الأزرار فقط.", reply_markup=PRIORITY_KB)
            return

        data["priority"] = text
        flow["step"] = "confirm"

        summary = order_summary(data) + "\nهل تريد تأكيد الطلب؟"
        update.message.reply_text(summary, reply_markup=CONFIRM_KB)
        return

    # التأكيد
    if step == "confirm":
        if text == "❌ إلغاء":
            reset_flow(context)
            update.message.reply_text("تم إلغاء الطلب.", reply_markup=main_keyboard(user_id))
            return

        if text == "✅ تأكيد":
            order_id = create_order(data)
            reset_flow(context)

            team_text = (
                f"🆕 طلب جديد رقم #{order_id}\n\n"
                f"👤 الوسيط: {data['intermediary_name']}\n"
                f"🏷 العميل النهائي: {data['final_client']}\n"
                f"🗂 الفئة: {data['category']}\n"
                f"📌 العنوان: {data['title']}\n"
                f"📝 التفاصيل: {data['details']}\n"
                f"💰 السعر: {data['price']} ريال\n"
                f"⏱ الموعد: {data['deadline']}\n"
                f"⚡ الأولوية: {data['priority']}\n\n"
                f"الأوامر:\n"
                f"/order {order_id}\n"
                f"/progress {order_id}\n"
                f"/review {order_id}\n"
                f"/done {order_id}\n"
                f"/cancelorder {order_id}\n"
                f"/assign {order_id} اسم_المسؤول"
            )

            update.message.reply_text(
                f"تم تسجيل الطلب بنجاح ✅\nرقم الطلب: #{order_id}",
                reply_markup=main_keyboard(user_id)
            )

            notify_team(context, team_text)
            notify_requester(
                context,
                data["requester_chat_id"],
                f"تم استلام طلبك بنجاح ✅\nرقم الطلب: #{order_id}\nاحتفظ بهذا الرقم للمتابعة."
            )
            return

        update.message.reply_text("اختر تأكيد أو إلغاء فقط.", reply_markup=CONFIRM_KB)
        return

    update.message.reply_text("اختر من القائمة.", reply_markup=main_keyboard(user_id))

# =========================
# MAIN
# =========================
def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN is missing")

    init_db()
    keep_alive()

    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", cmd_start))
    dp.add_handler(CommandHandler("help", cmd_help))
    dp.add_handler(CommandHandler("cancel", cmd_cancel))
    dp.add_handler(CommandHandler("queue", cmd_queue))
    dp.add_handler(CommandHandler("order", cmd_order))
    dp.add_handler(CommandHandler("progress", cmd_progress))
    dp.add_handler(CommandHandler("review", cmd_review))
    dp.add_handler(CommandHandler("done", cmd_done))
    dp.add_handler(CommandHandler("cancelorder", cmd_cancelorder))
    dp.add_handler(CommandHandler("assign", cmd_assign))
    dp.add_handler(CommandHandler("id", cmd_id))

    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_text))

    logging.info("Bot running...")
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
