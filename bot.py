import os
import re
import sqlite3
import threading
import logging
from datetime import datetime, date, timedelta

from flask import Flask
from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

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
                request_mode TEXT NOT NULL,
                requester_chat_id INTEGER NOT NULL,
                requester_user_id INTEGER NOT NULL,
                requester_name TEXT,
                requester_username TEXT,
                intermediary_name TEXT NOT NULL,
                final_client TEXT,
                category TEXT,
                title TEXT NOT NULL,
                details TEXT,
                raw_text TEXT,
                price REAL,
                deadline TEXT,
                priority TEXT,
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
                request_mode,
                requester_chat_id, requester_user_id, requester_name, requester_username,
                intermediary_name, final_client, category, title, details, raw_text,
                price, deadline, priority, status, assignee, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now_text(), now_text(),
                data.get("request_mode", "quick"),
                data["requester_chat_id"], data["requester_user_id"], data.get("requester_name"), data.get("requester_username"),
                data["intermediary_name"], data.get("final_client"), data.get("category"), data["title"], data.get("details"), data.get("raw_text"),
                data.get("price"), data.get("deadline"), data.get("priority"), "new", data.get("assignee"), data.get("notes"),
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

def get_intermediary_report(period="all"):
    where_clause = ""
    if period == "today":
        where_clause = "WHERE date(created_at) = date('now', 'localtime')"
    elif period == "week":
        where_clause = "WHERE date(created_at) >= date('now', '-6 day', 'localtime')"

    with db_conn() as conn:
        return conn.execute(
            f"""
            SELECT
                intermediary_name,
                COUNT(*) AS orders_count,
                COALESCE(SUM(price), 0) AS total_value,
                SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END) AS done_count,
                SUM(CASE WHEN status IN ('new', 'in_progress', 'waiting_review') THEN 1 ELSE 0 END) AS open_count
            FROM orders
            {where_clause}
            GROUP BY intermediary_name
            ORDER BY orders_count DESC, total_value DESC
            """
        ).fetchall()

# =========================
# KEEP ALIVE
# =========================
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running"

@app.route("/healthz")
def healthz():
    return "ok"

def run_web():
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    t = threading.Thread(target=run_web, daemon=True)
    t.start()

# =========================
# DATA / TEXT
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

MAIN_USER_KB = ReplyKeyboardMarkup(
    [
        ["⚡ طلب سريع"],
        ["🧭 طلب منظم"],
        ["🔎 متابعة طلب"],
    ],
    resize_keyboard=True
)

MAIN_ADMIN_KB = ReplyKeyboardMarkup(
    [
        ["⚡ طلب سريع"],
        ["🧭 طلب منظم"],
        ["🔎 متابعة طلب"],
        ["📋 قائمة الطلبات"],
        ["📊 تقرير الوسطاء"],
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

def is_admin(user_id):
    return user_id in ADMIN_IDS

def main_keyboard(user_id):
    return MAIN_ADMIN_KB if is_admin(user_id) else MAIN_USER_KB

def reset_flow(context: CallbackContext):
    context.user_data.pop("workflow", None)

def current_flow(context: CallbackContext):
    return context.user_data.get("workflow")

def start_flow(context: CallbackContext, mode: str):
    context.user_data["workflow"] = {
        "mode": mode,
        "step": None,
        "data": {},
    }

def parse_price(text):
    cleaned = text.strip().replace("ريال", "").replace("SAR", "").replace(",", ".")
    cleaned = re.sub(r"[^\d.]", "", cleaned)
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None

def arabic_weekday(d: date):
    names = {
        0: "الاثنين",
        1: "الثلاثاء",
        2: "الأربعاء",
        3: "الخميس",
        4: "الجمعة",
        5: "السبت",
        6: "الأحد",
    }
    return names[d.weekday()]

def deadline_keyboard():
    today = date.today()
    rows = []
    mapping = {}
    row = []
    for i in range(7):
        d = today + timedelta(days=i)
        label = "اليوم" if i == 0 else ("غدًا" if i == 1 else arabic_weekday(d))
        button_text = f"{label} {d.day}/{d.month}"
        mapping[button_text] = d.strftime("%Y-%m-%d")
        row.append(button_text)
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(["🔙 رجوع"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True), mapping

def date_text_for_group(iso_date: str):
    try:
        d = datetime.strptime(iso_date, "%Y-%m-%d").date()
        return f"{arabic_weekday(d)} {d.day}/{d.month}/{d.year}"
    except Exception:
        return iso_date

def compact_lines(text):
    lines = []
    for line in text.splitlines():
        t = line.strip()
        if not t:
            continue
        t = re.sub(r"^\[[^\]]+\]\s*[^:]+:\s*", "", t)
        lines.append(t)
    return lines

def is_filler_line(line):
    fillers = [
        "السلام عليكم", "وعليكم السلام", "الله يسعدك", "الله يرضى عليك", "مشكوره", "مشكور",
        "ياعسل", "يا قلبي", "ياقلبي", "طيب", "تمام", "باذن الله", "إن شاء الله", "ان شاء الله",
        "مشكوره يالحب", "شكرًا", "شكرا", "لا تشيلي هم", "الله يرضى عليك"
    ]
    low = line.replace("ـ", "").strip().lower()
    return any(f in low for f in fillers)

def guess_service_type(lines):
    joined = " ".join(lines)
    keywords = [
        ("دعوة درس تطبيقي", ["دعوة", "درس تطبيقي"]),
        ("عرض بوربوينت", ["بوربوينت"]),
        ("فيديو", ["فيديو"]),
        ("ملف إنجاز", ["ملف إنجاز", "إنجاز"]),
        ("ورقة عمل", ["ورقة عمل"]),
        ("اختبار", ["اختبار"]),
        ("تصميم", ["تصميم", "تصاميم"]),
    ]
    for title, keys in keywords:
        if any(k in joined for k in keys):
            return title
    return "طلب تعليمي"

def extract_school_info(lines):
    name = ""
    school = ""
    manager = ""
    grade = ""
    lesson = ""
    strategies = []
    needs = []

    grade_patterns = [
        r"(أول|ثاني|ثالث|رابع|خامس|سادس)\s+ابتدائي",
        r"(الأول|الثاني|الثالث|الرابع|الخامس|السادس)\s+ابتدائي",
    ]

    for line in lines:
        low = line.replace("،", " ").replace(":", " ").strip()

        if not name and "اسمي" in low:
            name = low.split("اسمي")[-1].strip(" :")
        if "المدرسة" in low and not school:
            school = low.split("المدرسة")[-1].strip(" :")
        if ("مديرة" in low or "مدير" in low) and not manager:
            parts = low.split()
            if parts:
                manager = parts[-1]
        if "الدرس" in low and not lesson:
            lesson = low.split("الدرس")[-1].strip(" :")
        if "استراتيجية" in low or "استراتيجيات" in low:
            strategies.append(low)
        if any(k in low for k in ["ذكاء اصطناعي", "ألعاب", "أناشيد", "إبداع", "إبداعي", "فيديو", "دعوة"]):
            needs.append(low)

        for pat in grade_patterns:
            m = re.search(pat, low)
            if m and not grade:
                grade = m.group(0)

    return {
        "name": name,
        "school": school,
        "manager": manager,
        "grade": grade,
        "lesson": lesson,
        "strategies": strategies,
        "needs": needs,
    }

def summarize_quick_text(raw_text):
    lines = compact_lines(raw_text)
    useful = [ln for ln in lines if not is_filler_line(ln)]
    extracted = extract_school_info(useful)
    service_type = guess_service_type(useful)

    top_lines = []
    for ln in useful:
        if len(top_lines) >= 8:
            break
        top_lines.append(ln)

    cleaned_excerpt = "\n".join(f"• {ln}" for ln in top_lines) if top_lines else "• لا يوجد نص واضح"

    summary = (
        f"🧹 ملخص تلقائي للرسالة:\n\n"
        f"📌 نوع العمل: {service_type}\n"
        f"👤 الاسم: {extracted['name'] or 'غير واضح'}\n"
        f"🏫 المدرسة: {extracted['school'] or 'غير واضحة'}\n"
        f"👩‍💼/👨‍💼 الإدارة: {extracted['manager'] or 'غير واضحة'}\n"
        f"📚 الصف/المرحلة: {extracted['grade'] or 'غير واضح'}\n"
        f"📖 الدرس/الموضوع: {extracted['lesson'] or 'غير واضح'}\n"
    )

    if extracted["needs"]:
        unique_needs = []
        for n in extracted["needs"]:
            if n not in unique_needs:
                unique_needs.append(n)
        summary += "\n🧠 المتطلبات المستخرجة:\n" + "\n".join(f"• {n}" for n in unique_needs[:8]) + "\n"

    if extracted["strategies"]:
        summary += "\n🪄 الاستراتيجيات:\n" + "\n".join(f"• {s}" for s in extracted["strategies"][:8]) + "\n"

    summary += f"\n🧾 النص المنظف:\n{cleaned_excerpt}"
    return summary, extracted

def order_summary(data):
    mode_label = "سريع" if data.get("request_mode") == "quick" else "منظم"
    price_val = data.get("price")
    price_txt = f"{price_val:.2f} ريال" if isinstance(price_val, (int, float)) else "-"
    deadline_txt = date_text_for_group(data["deadline"]) if data.get("deadline") else "-"
    return (
        f"📋 ملخص الطلب ({mode_label}):\n\n"
        f"👤 الوسيط: {data.get('intermediary_name', '-')}\n"
        f"🏷 العميل النهائي: {data.get('final_client', '-')}\n"
        f"🗂 الفئة: {data.get('category', '-')}\n"
        f"📌 عنوان الطلب: {data.get('title', '-')}\n"
        f"📝 التفاصيل: {data.get('details', '-')}\n"
        f"💰 السعر: {price_txt}\n"
        f"⏱ الموعد: {deadline_txt}\n"
        f"⚡ الأولوية: {data.get('priority', '-')}\n"
    )

def order_full_text(order):
    return (
        f"🧾 رقم الطلب: #{order['id']}\n"
        f"📅 وقت الإنشاء: {order['created_at']}\n"
        f"🕒 آخر تحديث: {order['updated_at']}\n"
        f"🧭 نوع الإدخال: {order['request_mode']}\n\n"
        f"👤 الوسيط: {order['intermediary_name']}\n"
        f"🏷 العميل النهائي: {order['final_client'] or '-'}\n"
        f"🗂 الفئة: {order['category'] or '-'}\n"
        f"📌 العنوان: {order['title']}\n"
        f"📝 التفاصيل: {order['details'] or '-'}\n"
        f"💰 السعر: {order['price'] or '-'} ريال\n"
        f"⏱ الموعد: {order['deadline'] or '-'}\n"
        f"⚡ الأولوية: {order['priority'] or '-'}\n"
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
        "• طلب سريع = تلصق المحادثة كما هي\n"
        "• طلب منظم = خطوات واضحة وأزرار للتاريخ\n\n"
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
        "/report [today|week] - تقرير الوسطاء\n"
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

    if not context.args or not context.args[0].isdigit():
        update.message.reply_text("استخدم: /order 12", reply_markup=main_keyboard(user_id))
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

    if not context.args or not context.args[0].isdigit():
        update.message.reply_text("استخدم: /command <id>", reply_markup=main_keyboard(user_id))
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

    if len(context.args) < 2 or not context.args[0].isdigit():
        update.message.reply_text("استخدم: /assign 12 فيصل", reply_markup=main_keyboard(user_id))
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

def cmd_report(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        update.message.reply_text("هذا الأمر مخصص للإدارة فقط.", reply_markup=main_keyboard(user_id))
        return

    period = "all"
    if context.args:
        arg = context.args[0].strip().lower()
        if arg in ["اليوم", "today"]:
            period = "today"
        elif arg in ["الأسبوع", "week"]:
            period = "week"

    label = "الكل" if period == "all" else ("اليوم" if period == "today" else "الأسبوع")
    rows = get_intermediary_report(period)

    if not rows:
        update.message.reply_text(f"لا توجد بيانات في تقرير {label}.", reply_markup=main_keyboard(user_id))
        return

    lines = [f"📊 تقرير الوسطاء ({label})\n"]
    grand_total = 0.0

    for r in rows:
        total_value = float(r["total_value"] or 0)
        grand_total += total_value
        intermediary_share = total_value / 2
        team_share = total_value / 2

        lines.append(
            f"👤 الوسيط: {r['intermediary_name']}\n"
            f"• عدد الطلبات: {r['orders_count']}\n"
            f"• إجمالي المبالغ: {total_value:.2f} ريال\n"
            f"• نصيب الوسيط (50%): {intermediary_share:.2f} ريال\n"
            f"• نصيب الفريق (50%): {team_share:.2f} ريال\n"
            f"• المنجزة: {r['done_count']}\n"
            f"• المفتوحة: {r['open_count']}\n"
        )

    lines.append(f"📌 الإجمالي العام: {grand_total:.2f} ريال")
    update.message.reply_text("\n".join(lines), reply_markup=main_keyboard(user_id))

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

    if text == "⚡ طلب سريع":
        start_flow(context, "quick")
        flow = current_flow(context)
        flow["step"] = "q_name"
        flow["data"] = {
            "requester_chat_id": chat_id,
            "requester_user_id": user_id,
            "requester_name": update.effective_user.full_name or "",
            "requester_username": update.effective_user.username or "",
            "request_mode": "quick",
        }
        update.message.reply_text("اكتب اسمك:", reply_markup=main_keyboard(user_id))
        return

    if text == "🧭 طلب منظم":
        start_flow(context, "guided")
        flow = current_flow(context)
        flow["step"] = "g_intermediary_name"
        flow["data"] = {
            "requester_chat_id": chat_id,
            "requester_user_id": user_id,
            "requester_name": update.effective_user.full_name or "",
            "requester_username": update.effective_user.username or "",
            "request_mode": "guided",
        }
        update.message.reply_text("اكتب اسمك:", reply_markup=main_keyboard(user_id))
        return

    if text == "🔎 متابعة طلب":
        context.user_data["workflow"] = {
            "mode": "track",
            "step": "track_order",
            "data": {},
        }
        update.message.reply_text("أرسل رقم الطلب فقط:", reply_markup=main_keyboard(user_id))
        return

    flow = current_flow(context)
    if not flow:
        update.message.reply_text("اختر من القائمة.", reply_markup=main_keyboard(user_id))
        return

    mode = flow.get("mode")
    step = flow.get("step")
    data = flow.get("data", {})

    # TRACK
    if mode == "track" and step == "track_order":
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

    # QUICK FLOW
    if mode == "quick":
        if step == "q_name":
            data["intermediary_name"] = text
            flow["step"] = "q_raw"
            update.message.reply_text(
                "ألصق الرسالة/المحادثة كاملة هنا في رسالة واحدة، حتى لو كانت طويلة.",
                reply_markup=main_keyboard(user_id)
            )
            return

        if step == "q_raw":
            data["raw_text"] = text
            cleaned_summary, extracted = summarize_quick_text(text)
            data["title"] = extracted["name"] if extracted["name"] else "طلب سريع"
            data["details"] = cleaned_summary
            data["category"] = guess_service_type(compact_lines(text))
            flow["step"] = "q_price"
            update.message.reply_text(
                cleaned_summary + "\n\nاكتب السعر المتفق عليه بالأرقام فقط:",
                reply_markup=main_keyboard(user_id)
            )
            return

        if step == "q_price":
            price = parse_price(text)
            if price is None:
                update.message.reply_text("اكتب السعر بالأرقام فقط، مثل: 25", reply_markup=main_keyboard(user_id))
                return
            data["price"] = price
            flow["step"] = "q_deadline"
            deadline_kb, mapping = deadline_keyboard()
            data["deadline_map"] = mapping
            update.message.reply_text("اختر تاريخ التسليم:", reply_markup=deadline_kb)
            return

        if step == "q_deadline":
            mapping = data.get("deadline_map", {})
            if text not in mapping:
                deadline_kb, mapping = deadline_keyboard()
                data["deadline_map"] = mapping
                update.message.reply_text("اختر التاريخ من الأزرار فقط:", reply_markup=deadline_kb)
                return

            data["deadline"] = mapping[text]
            flow["step"] = "q_priority"
            update.message.reply_text("اختر الأولوية:", reply_markup=PRIORITY_KB)
            return

        if step == "q_priority":
            if text not in ["عادي", "عاجل"]:
                update.message.reply_text("اختر الأولوية من الأزرار فقط.", reply_markup=PRIORITY_KB)
                return

            data["priority"] = text
            flow["step"] = "q_confirm"
            summary = order_summary(data) + "\nهل تريد تأكيد الطلب؟"
            update.message.reply_text(summary, reply_markup=CONFIRM_KB)
            return

        if step == "q_confirm":
            if text == "❌ إلغاء":
                reset_flow(context)
                update.message.reply_text("تم إلغاء الطلب.", reply_markup=main_keyboard(user_id))
                return

            if text == "✅ تأكيد":
                order_id = create_order(data)
                reset_flow(context)

                team_text = (
                    f"🆕 طلب سريع جديد رقم #{order_id}\n\n"
                    f"👤 الوسيط: {data['intermediary_name']}\n"
                    f"🗂 الفئة: {data.get('category', '-')}\n"
                    f"📌 العنوان: {data['title']}\n"
                    f"💰 السعر: {data['price']} ريال\n"
                    f"⏱ الموعد: {date_text_for_group(data['deadline'])}\n"
                    f"⚡ الأولوية: {data['priority']}\n\n"
                    f"🧹 الملخص:\n{data['details']}\n"
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

    # GUIDED FLOW
    if mode == "guided":
        if step == "g_intermediary_name":
            data["intermediary_name"] = text
            flow["step"] = "g_final_client"
            update.message.reply_text("اكتب اسم صاحب العمل النهائي / العميل:", reply_markup=main_keyboard(user_id))
            return

        if step == "g_final_client":
            data["final_client"] = text
            flow["step"] = "g_category"
            update.message.reply_text("اختر الفئة الرئيسية:", reply_markup=CATEGORY_KB)
            return

        if step == "g_category":
            if text == "أخرى":
                flow["step"] = "g_custom_category"
                update.message.reply_text("اكتب اسم الفئة التي تريدها:", reply_markup=main_keyboard(user_id))
                return

            if text not in CATEGORIES:
                update.message.reply_text("اختر الفئة من الأزرار فقط.", reply_markup=CATEGORY_KB)
                return

            data["category"] = text
            flow["step"] = "g_title"
            update.message.reply_text("اكتب اسم الملف أو عنوان الطلب:", reply_markup=main_keyboard(user_id))
            return

        if step == "g_custom_category":
            data["category"] = text
            flow["step"] = "g_title"
            update.message.reply_text("اكتب اسم الملف أو عنوان الطلب:", reply_markup=main_keyboard(user_id))
            return

        if step == "g_title":
            data["title"] = text
            flow["step"] = "g_details"
            update.message.reply_text(
                "اكتب التفاصيل كاملة:\n"
                "مثال: الصف، الدرس، نوع الإخراج، ملاحظات العميل، أي إضافات.",
                reply_markup=main_keyboard(user_id)
            )
            return

        if step == "g_details":
            data["details"] = text
            flow["step"] = "g_price"
            update.message.reply_text("اكتب السعر المتفق عليه بالأرقام فقط:", reply_markup=main_keyboard(user_id))
            return

        if step == "g_price":
            price = parse_price(text)
            if price is None:
                update.message.reply_text("اكتب السعر بالأرقام فقط، مثل: 25", reply_markup=main_keyboard(user_id))
                return

            data["price"] = price
            flow["step"] = "g_deadline"
            deadline_kb, mapping = deadline_keyboard()
            data["deadline_map"] = mapping
            update.message.reply_text("اختر تاريخ التسليم:", reply_markup=deadline_kb)
            return

        if step == "g_deadline":
            mapping = data.get("deadline_map", {})
            if text not in mapping:
                deadline_kb, mapping = deadline_keyboard()
                data["deadline_map"] = mapping
                update.message.reply_text("اختر التاريخ من الأزرار فقط:", reply_markup=deadline_kb)
                return

            data["deadline"] = mapping[text]
            flow["step"] = "g_priority"
            update.message.reply_text("اختر الأولوية:", reply_markup=PRIORITY_KB)
            return

        if step == "g_priority":
            if text not in ["عادي", "عاجل"]:
                update.message.reply_text("اختر الأولوية من الأزرار فقط.", reply_markup=PRIORITY_KB)
                return

            data["priority"] = text
            flow["step"] = "g_confirm"
            summary = order_summary(data) + "\nهل تريد تأكيد الطلب؟"
            update.message.reply_text(summary, reply_markup=CONFIRM_KB)
            return

        if step == "g_confirm":
            if text == "❌ إلغاء":
                reset_flow(context)
                update.message.reply_text("تم إلغاء الطلب.", reply_markup=main_keyboard(user_id))
                return

            if text == "✅ تأكيد":
                order_id = create_order(data)
                reset_flow(context)

                team_text = (
                    f"🆕 طلب منظم جديد رقم #{order_id}\n\n"
                    f"👤 الوسيط: {data['intermediary_name']}\n"
                    f"🏷 العميل النهائي: {data['final_client']}\n"
                    f"🗂 الفئة: {data['category']}\n"
                    f"📌 العنوان: {data['title']}\n"
                    f"📝 التفاصيل: {data['details']}\n"
                    f"💰 السعر: {data['price']} ريال\n"
                    f"⏱ الموعد: {date_text_for_group(data['deadline'])}\n"
                    f"⚡ الأولوية: {data['priority']}\n"
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
    dp.add_handler(CommandHandler("report", cmd_report))
    dp.add_handler(CommandHandler("id", cmd_id))

    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_text))

    logging.info("Bot running...")
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
