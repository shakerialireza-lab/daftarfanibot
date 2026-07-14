"""
ربات آرشیو موضوعی تلگرام
--------------------------------
هر پیامی که کاربر برای ربات فوروارد/ارسال کند، ربات آن را در یک «موضوع» (Topic)
دسته‌بندی و ذخیره می‌کند. کاربر می‌تواند بعداً پیام‌های هر موضوع را مرور یا جستجو کند.

دو روش دسته‌بندی:
  1) خودکار: اگر متن/کپشن پیام شامل هشتگ باشد (مثلاً «#کتاب»)، همان هشتگ به عنوان
     نام موضوع استفاده می‌شود.
  2) دستی: اگر هشتگی وجود نداشته باشد، ربات یک کیبورد شیشه‌ای (inline keyboard) از
     موضوعات موجود + گزینه‌ی «موضوع جدید» نشان می‌دهد تا کاربر انتخاب کند.

دستورات:
  /start          - راهنما
  /topics         - لیست موضوعات و تعداد آیتم هر کدام
  /newtopic نام   - ساخت موضوع جدید
  /search عبارت   - جستجو در متن/کپشن پیام‌های ذخیره‌شده
  /cancel         - لغو انتخاب موضوع در حال انجام
"""

import logging
import re
import sqlite3
from datetime import datetime

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ---------------------------------------------------------------------------
# تنظیمات
# ---------------------------------------------------------------------------
BOT_TOKEN = "PUT_YOUR_BOT_TOKEN_HERE"  # توکنی که از @BotFather گرفتی اینجا بذار
DB_PATH = "archive.db"

HASHTAG_RE = re.compile(r"#([\w\u0600-\u06FF_]{2,64})")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# دیتابیس
# ---------------------------------------------------------------------------

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS topics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(user_id, name)
        );

        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            topic_id INTEGER NOT NULL,
            content_type TEXT NOT NULL,   -- text, photo, video, document, voice, audio, link
            text_content TEXT,           -- متن پیام یا کپشن
            file_id TEXT,                -- برای مدیا
            source_chat TEXT,            -- از کجا فوروارد شده (اختیاری)
            created_at TEXT NOT NULL,
            FOREIGN KEY(topic_id) REFERENCES topics(id)
        );
        """
    )
    conn.commit()
    conn.close()


def get_or_create_topic(user_id: int, name: str) -> int:
    name = name.strip()
    conn = get_conn()
    cur = conn.execute(
        "SELECT id FROM topics WHERE user_id=? AND name=?", (user_id, name)
    )
    row = cur.fetchone()
    if row:
        topic_id = row["id"]
    else:
        cur = conn.execute(
            "INSERT INTO topics (user_id, name, created_at) VALUES (?, ?, ?)",
            (user_id, name, datetime.utcnow().isoformat()),
        )
        conn.commit()
        topic_id = cur.lastrowid
    conn.close()
    return topic_id


def list_topics(user_id: int):
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT t.id, t.name, COUNT(i.id) as cnt
        FROM topics t LEFT JOIN items i ON i.topic_id = t.id
        WHERE t.user_id = ?
        GROUP BY t.id
        ORDER BY t.name COLLATE NOCASE
        """,
        (user_id,),
    ).fetchall()
    conn.close()
    return rows


def save_item(user_id, topic_id, content_type, text_content, file_id, source_chat):
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO items (user_id, topic_id, content_type, text_content, file_id, source_chat, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            topic_id,
            content_type,
            text_content,
            file_id,
            source_chat,
            datetime.utcnow().isoformat(),
        ),
    )
    conn.commit()
    conn.close()


def get_topic_items(user_id: int, topic_id: int, limit=20, offset=0):
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT * FROM items
        WHERE user_id=? AND topic_id=?
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
        """,
        (user_id, topic_id, limit, offset),
    ).fetchall()
    conn.close()
    return rows


def search_items(user_id: int, query: str, limit=20):
    conn = get_conn()
    like = f"%{query}%"
    rows = conn.execute(
        """
        SELECT i.*, t.name as topic_name FROM items i
        JOIN topics t ON t.id = i.topic_id
        WHERE i.user_id=? AND i.text_content LIKE ?
        ORDER BY i.created_at DESC
        LIMIT ?
        """,
        (user_id, like, limit),
    ).fetchall()
    conn.close()
    return rows


def get_topic_by_id(user_id: int, topic_id: int):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM topics WHERE user_id=? AND id=?", (user_id, topic_id)
    ).fetchone()
    conn.close()
    return row


# ---------------------------------------------------------------------------
# کمکی: استخراج محتوا از پیام تلگرام
# ---------------------------------------------------------------------------

def extract_content(message):
    """برمی‌گرداند: content_type, text_content, file_id"""
    caption_or_text = message.text or message.caption or ""

    if message.photo:
        return "photo", caption_or_text, message.photo[-1].file_id
    if message.video:
        return "video", caption_or_text, message.video.file_id
    if message.document:
        return "document", caption_or_text, message.document.file_id
    if message.voice:
        return "voice", caption_or_text, message.voice.file_id
    if message.audio:
        return "audio", caption_or_text, message.audio.file_id
    if message.text:
        return "text", message.text, None
    return "text", caption_or_text or "(بدون متن)", None


def source_chat_name(message):
    if message.forward_from_chat:
        return message.forward_from_chat.title or message.forward_from_chat.username
    if message.forward_from:
        return message.forward_from.full_name
    return None


# ---------------------------------------------------------------------------
# هندلرها
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "سلام! 👋\n\n"
        "من پیام‌هایی که برام بفرستی یا فوروارد کنی رو *موضوع‌بندی* و ذخیره می‌کنم.\n\n"
        "روش‌ها:\n"
        "1️⃣ اگه توی متن یا کپشن پیام یه هشتگ بذاری (مثلاً «#کتاب»)، خودم با همون موضوع ذخیره می‌کنم.\n"
        "2️⃣ اگه هشتگ نذاری، یه لیست از موضوع‌های قبلیت نشون میدم که انتخاب کنی، یا موضوع جدید بسازی.\n\n"
        "دستورات:\n"
        "/topics – لیست موضوع‌ها و تعداد آیتم‌ها\n"
        "/newtopic نام\\_موضوع – ساخت موضوع جدید\n"
        "/search عبارت – جستجو در محتوای ذخیره‌شده\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def topics_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    rows = list_topics(user_id)
    if not rows:
        await update.message.reply_text(
            "هنوز هیچ موضوعی نساختی. یه پیام بفرست تا شروع کنیم!"
        )
        return
    buttons = [
        [InlineKeyboardButton(f"{r['name']} ({r['cnt']})", callback_data=f"view:{r['id']}:0")]
        for r in rows
    ]
    await update.message.reply_text(
        "📂 موضوع‌های تو:", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def newtopic_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("استفاده: /newtopic نام موضوع")
        return
    name = " ".join(context.args)
    user_id = update.effective_user.id
    get_or_create_topic(user_id, name)
    await update.message.reply_text(f"✅ موضوع «{name}» ساخته شد (یا از قبل وجود داشت).")


async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("استفاده: /search عبارت")
        return
    query = " ".join(context.args)
    user_id = update.effective_user.id
    rows = search_items(user_id, query)
    if not rows:
        await update.message.reply_text("چیزی پیدا نشد.")
        return
    lines = [f"🔎 نتایج برای «{query}»:\n"]
    for r in rows:
        snippet = (r["text_content"] or "")[:80]
        lines.append(f"• [{r['topic_name']}] {snippet}")
    await update.message.reply_text("\n".join(lines))


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("pending_item", None)
    await update.message.reply_text("لغو شد.")


PAGE_SIZE = 10


async def incoming_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user_id = update.effective_user.id
    content_type, text_content, file_id = extract_content(message)
    src = source_chat_name(message)

    match = HASHTAG_RE.search(text_content or "")
    if match:
        topic_name = match.group(1)
        topic_id = get_or_create_topic(user_id, topic_name)
        save_item(user_id, topic_id, content_type, text_content, file_id, src)
        await message.reply_text(f"✅ ذخیره شد در موضوع «{topic_name}»")
        return

    # هشتگ نداره -> باید کاربر موضوع رو انتخاب کنه
    context.user_data["pending_item"] = {
        "content_type": content_type,
        "text_content": text_content,
        "file_id": file_id,
        "source_chat": src,
    }

    rows = list_topics(user_id)
    buttons = [
        [InlineKeyboardButton(r["name"], callback_data=f"pick:{r['id']}")] for r in rows
    ]
    buttons.append([InlineKeyboardButton("➕ موضوع جدید", callback_data="pick:new")])
    await message.reply_text(
        "این پیام رو توی کدوم موضوع ذخیره کنم؟",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    data = query.data
    await query.answer()

    if data.startswith("pick:"):
        choice = data.split(":", 1)[1]
        pending = context.user_data.get("pending_item")
        if not pending:
            await query.edit_message_text("این درخواست منقضی شده. یه پیام جدید بفرست.")
            return
        if choice == "new":
            context.user_data["awaiting_new_topic_name"] = True
            await query.edit_message_text("اسم موضوع جدید رو بفرست (فقط متن):")
            return
        topic_id = int(choice)
        save_item(
            user_id,
            topic_id,
            pending["content_type"],
            pending["text_content"],
            pending["file_id"],
            pending["source_chat"],
        )
        context.user_data.pop("pending_item", None)
        topic = get_topic_by_id(user_id, topic_id)
        await query.edit_message_text(f"✅ ذخیره شد در موضوع «{topic['name']}»")
        return

    if data.startswith("view:"):
        _, topic_id, offset = data.split(":")
        topic_id, offset = int(topic_id), int(offset)
        topic = get_topic_by_id(user_id, topic_id)
        if not topic:
            await query.edit_message_text("موضوع پیدا نشد.")
            return
        items = get_topic_items(user_id, topic_id, limit=PAGE_SIZE, offset=offset)
        if not items:
            await query.edit_message_text(f"موضوع «{topic['name']}» خالیه.")
            return
        lines = [f"📁 موضوع: {topic['name']} (صفحه {offset // PAGE_SIZE + 1})\n"]
        for it in items:
            snippet = (it["text_content"] or f"[{it['content_type']}]")[:100]
            lines.append(f"• {snippet}")
        nav = []
        if offset > 0:
            nav.append(
                InlineKeyboardButton("⬅️ قبلی", callback_data=f"view:{topic_id}:{max(0, offset - PAGE_SIZE)}")
            )
        if len(items) == PAGE_SIZE:
            nav.append(
                InlineKeyboardButton("بعدی ➡️", callback_data=f"view:{topic_id}:{offset + PAGE_SIZE}")
            )
        markup = InlineKeyboardMarkup([nav]) if nav else None
        await query.edit_message_text("\n".join(lines), reply_markup=markup)
        return


async def new_topic_name_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """وقتی کاربر بعد از زدن «موضوع جدید» اسم موضوع رو تایپ می‌کنه."""
    if not context.user_data.get("awaiting_new_topic_name"):
        await incoming_message(update, context)
        return
    user_id = update.effective_user.id
    name = update.message.text.strip()
    context.user_data["awaiting_new_topic_name"] = False
    topic_id = get_or_create_topic(user_id, name)
    pending = context.user_data.get("pending_item")
    if pending:
        save_item(
            user_id,
            topic_id,
            pending["content_type"],
            pending["text_content"],
            pending["file_id"],
            pending["source_chat"],
        )
        context.user_data.pop("pending_item", None)
        await update.message.reply_text(f"✅ موضوع «{name}» ساخته شد و پیام ذخیره شد.")
    else:
        await update.message.reply_text(f"✅ موضوع «{name}» ساخته شد.")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("topics", topics_cmd))
    app.add_handler(CommandHandler("newtopic", newtopic_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("cancel", cancel_cmd))

    app.add_handler(CallbackQueryHandler(callback_handler))

    # هر پیام متنی معمولی (که دستور نیست) اول چک میشه ببینه منتظر اسم موضوع جدیده یا نه
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, new_topic_name_handler))
    # پیام‌های مدیا (عکس/ویدیو/فایل/صوت)
    app.add_handler(
        MessageHandler(
            filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.VOICE | filters.AUDIO,
            incoming_message,
        )
    )

    logger.info("ربات شروع به کار کرد...")
    app.run_polling()


if __name__ == "__main__":
    main()
