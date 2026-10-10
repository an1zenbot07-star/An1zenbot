import asyncio
import html as _html
import io
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, BotCommand,
    InputMediaPhoto, InputMediaVideo,
    InlineQuery, InlineQueryResultArticle, InlineQueryResultCachedPhoto, InputTextMessageContent,
    ChosenInlineResult
)
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
import aiohttp
from aiohttp import web

# ============ SOZLAMALAR ============
BOT_TOKEN = os.environ.get("BOT_TOKEN", "BOT_TOKEN_BU_YERGA")
# Bosh admin (owner): uning adminligini hech kim olib tashlay olmaydi
OWNER_USERNAME = "anituz_org"
INITIAL_ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "8470314807").split(",")]
DB_PATH = "anime_bot.db"

PAYMENT_CARD = "7777 0105 7309 7248"
PAYMENT_CARD_OWNER = "M.Abduvahobov"
ADMIN_USERNAME = "@an1zen"
# VIP to'lov cheklari shu adminning shaxsiy lichkasiga keladi
# (u admin botga kamida bir marta /start bosgan bo'lishi kerak)
RECEIPT_ADMIN_ID = int(os.environ.get("RECEIPT_ADMIN_ID", "8670829849"))
# "‼️Murojat uchun" tugmasi shu odamning profiliga olib boradi
CONTACT_USER_ID = int(os.environ.get("CONTACT_USER_ID", str(RECEIPT_ADMIN_ID)))
CHANNEL_USERNAME = "@an1zenuz"
CHANNEL_URL = "https://t.me/an1zenuz"
VIP_PLANS = [
    ("1hafta", "1 haftalik", "10 000 uzs"),
    ("1oy", "1 oylik", "15 000 uzs"),
    ("3oy", "3 oylik", "39 000 uzs"),
    ("5oy", "5 oylik", "49 000 uzs"),
    ("7oy", "7 oylik", "65 000 uzs"),
    ("8oy", "8 oylik", "75 000 uzs"),
]
MONTH_TO_DAYS = {"1hafta": 7, "1oy": 30, "3oy": 90, "5oy": 150, "7oy": 210, "8oy": 240}
LIST_PAGE_SIZE = 10  # Obunalarim / Yoqqanlar / Keyinroq: bitta bo'limda nechta anime
EP_PAGE_SIZE = 10    # qismlar: bitta bo'limda nechta qism tugmasi

ANNOUNCE_CHANNEL_ID = os.environ.get("ANNOUNCE_CHANNEL_ID", "-1004379465750")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "Major_dubbingbot")
PORT = int(os.environ.get("PORT", 10000))
ONLINE_WINDOW_MIN = 5
MONTHLY_WINDOW_DAYS = 30

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)


class SearchStates(StatesGroup):
    waiting_query = State()


class AdminFSM(StatesGroup):
    waiting_new_admin_id = State()
    waiting_ad_channel = State()
    waiting_delete_anime = State()
    waiting_delete_episode = State()
    waiting_changecode_old = State()
    waiting_changecode_new = State()
    waiting_postep = State()
    waiting_forcesub_forward = State()
    waiting_addseason = State()
    waiting_anime = State()
    waiting_postanime = State()
    waiting_addchannel = State()
    waiting_vipgive = State()
    waiting_edittag_find = State()
    waiting_edittag_text = State()
    waiting_edittag_confirm = State()
    waiting_delseason = State()
    waiting_vipcaption = State()
    waiting_vipcard = State()
    waiting_broadcast = State()
    waiting_news_date = State()
    waiting_news_body = State()
    waiting_news_confirm = State()
    waiting_hometext = State()
    waiting_tag = State()


# ============ DATABASE ============
def db_init():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS animes (
        code TEXT PRIMARY KEY, title TEXT, total_seasons INTEGER DEFAULT 1,
        total_episodes_declared INTEGER DEFAULT 0, genre TEXT, channel_name TEXT,
        quality TEXT, rating TEXT,
        views INTEGER DEFAULT 0, downloads INTEGER DEFAULT 0, is_premium INTEGER DEFAULT 0,
        poster_file_id TEXT, added_date TEXT, custom_caption TEXT
    )""")
    # Eski bazalarda yangi ustunlar yo'q bo'lishi mumkin — xavfsiz migratsiya
    for col, coltype in [("channel_name", "TEXT"), ("total_episodes_declared", "INTEGER DEFAULT 0"), ("custom_caption", "TEXT"), ("poster_url", "TEXT")]:
        try:
            cur.execute(f"ALTER TABLE animes ADD COLUMN {col} {coltype}")
        except sqlite3.OperationalError:
            pass
    cur.execute("""CREATE TABLE IF NOT EXISTS episodes (
        anime_code TEXT, season_number INTEGER, episode_number INTEGER, file_id TEXT, episode_title TEXT,
        PRIMARY KEY (anime_code, season_number, episode_number)
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, username TEXT, joined_date TEXT,
        is_vip INTEGER DEFAULT 0, vip_until TEXT, last_seen TEXT
    )""")
    try:
        cur.execute("ALTER TABLE users ADD COLUMN vip_plan TEXT")
    except sqlite3.OperationalError:
        pass
    cur.execute("""CREATE TABLE IF NOT EXISTS history (
        user_id INTEGER, anime_code TEXT, season_number INTEGER, episode_number INTEGER, watched_date TEXT,
        PRIMARY KEY (user_id, anime_code, season_number, episode_number)
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS admins (
        user_id INTEGER PRIMARY KEY, username TEXT, added_date TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY, value TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS force_sub_channels (
        channel_id INTEGER PRIMARY KEY, display_name TEXT, join_url TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS post_channels (
        id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER UNIQUE, title TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS user_lists (
        user_id INTEGER, anime_code TEXT, list_type TEXT, added_date TEXT,
        PRIMARY KEY (user_id, anime_code, list_type)
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS vip_receipts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, plan TEXT,
        file_unique_id TEXT, status TEXT DEFAULT 'pending', created TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS news_dates (
        id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT, sort_key INTEGER, created TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS news_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT, date_id INTEGER, title TEXT, body TEXT,
        photo_file_id TEXT, created TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS seasons (
        anime_code TEXT, season_number INTEGER,
        PRIMARY KEY (anime_code, season_number)
    )""")
    # Bir martalik migratsiya: eski animelar uchun fasllar jadvalini to'ldirish
    cur.execute("SELECT value FROM settings WHERE key='seasons_migrated'")
    if not cur.fetchone():
        old_animes = cur.execute("SELECT code, total_seasons FROM animes").fetchall()
        for a_code, total in old_animes:
            for sn in range(1, (total or 1) + 1):
                cur.execute("INSERT OR IGNORE INTO seasons (anime_code, season_number) VALUES (?,?)",
                            (a_code, sn))
        cur.execute("INSERT OR IGNORE INTO seasons (anime_code, season_number) "
                    "SELECT DISTINCT anime_code, season_number FROM episodes")
        cur.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('seasons_migrated', '1')")
    cur.execute("SELECT COUNT(*) FROM admins")
    if cur.fetchone()[0] == 0:
        for aid in INITIAL_ADMIN_IDS:
            cur.execute("INSERT OR IGNORE INTO admins (user_id, username, added_date) VALUES (?,?,?)",
                        (aid, str(aid), datetime.now().isoformat()))
    conn.commit()
    conn.close()


def db(query, params=(), fetch=None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(query, params)
    result = None
    if fetch == "one":
        result = cur.fetchone()
    elif fetch == "all":
        result = cur.fetchall()
    conn.commit()
    conn.close()
    return result


def get_seasons(code: str):
    rows = db("SELECT season_number FROM seasons WHERE anime_code=? ORDER BY season_number",
              (code,), "all")
    return [r[0] for r in rows]


def count_episodes(code: str) -> int:
    r = db("SELECT COUNT(*) FROM episodes WHERE anime_code=?", (code,), "one")
    return r[0] if r else 0


def render_qismi(text: str, count: int) -> str:
    """Captiondagi 'Qismi: ...' (yoki 'Qismlar: ...') qatorini haqiqiy qismlar soni bilan almashtiradi."""
    return re.sub(r"(?im)^([^\S\n]*)(qismi|qismlar)[^\S\n]*:.*$",
                  lambda m: f"{m.group(1)}Qismi: {count}", text)


def sync_total_seasons(code: str):
    n = len(get_seasons(code))
    db("UPDATE animes SET total_seasons=? WHERE code=?", (n, code))


def is_admin(user_id: int) -> bool:
    if user_id == get_owner_id():
        return True
    return bool(db("SELECT 1 FROM admins WHERE user_id=?", (user_id,), "one"))


def get_owner_id():
    row = db("SELECT value FROM settings WHERE key='owner_id'", (), "one")
    try:
        return int(row[0]) if row else None
    except (TypeError, ValueError):
        return None


def try_bind_owner(user_id: int, username: str):
    """Bosh admin @anituz_org botga birinchi marta yozganda uning ID si saqlanadi va u admin bo'ladi.
    Shundan keyin owner username emas, aynan shu ID bo'yicha aniqlanadi."""
    if get_owner_id() is None and (username or "").lower() == OWNER_USERNAME.lower():
        db("INSERT OR REPLACE INTO settings (key, value) VALUES ('owner_id', ?)", (str(user_id),))
        db("INSERT OR REPLACE INTO admins (user_id, username, added_date) VALUES (?,?,?)",
           (user_id, f"@{username}", datetime.now().isoformat()))


def ensure_user(user_id: int, username: str):
    now = datetime.now().isoformat()
    username = username or ""
    if not db("SELECT 1 FROM users WHERE user_id=?", (user_id,), "one"):
        db("INSERT INTO users (user_id, username, joined_date, last_seen) VALUES (?,?,?,?)",
           (user_id, username, now, now))
    elif username:
        db("UPDATE users SET last_seen=?, username=? WHERE user_id=?", (now, username, user_id))
    else:
        db("UPDATE users SET last_seen=? WHERE user_id=?", (now, user_id))
    if username:
        db("UPDATE admins SET username=? WHERE user_id=?", (f"@{username}", user_id))
    try_bind_owner(user_id, username)


class TrackUserMiddleware(BaseMiddleware):
    """Har bir xabar/tugmada foydalanuvchining username'ini yangilab turadi (VIP ro'yxatida ID emas, username chiqishi uchun)."""
    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user is not None and not user.is_bot:
            try:
                ensure_user(user.id, user.username)
            except Exception as e:
                logging.error(f"track user: {e}")
        return await handler(event, data)


dp.message.outer_middleware(TrackUserMiddleware())
dp.callback_query.outer_middleware(TrackUserMiddleware())


def is_vip(user_id: int) -> bool:
    row = db("SELECT is_vip, vip_until FROM users WHERE user_id=?", (user_id,), "one")
    if not row or not row[0]:
        return False
    if row[1] and row[1] != "forever":
        if datetime.fromisoformat(row[1]) < datetime.now():
            db("UPDATE users SET is_vip=0, vip_until=NULL WHERE user_id=?", (user_id,))
            return False
    return True


def get_setting(key: str, default: str = "") -> str:
    row = db("SELECT value FROM settings WHERE key=?", (key,), "one")
    return row[0] if row else default


def set_setting(key: str, value: str):
    db("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))


def get_channels():
    return db("SELECT id, chat_id, title FROM post_channels ORDER BY id", (), "all")


async def post_to_channel(chat_id, poster_file_id, caption: str, kb):
    """caption — tayyor HTML (iqtibos bilan)."""
    await send_rich(chat_id, poster_file_id, caption, kb)


def channel_error_text(e: Exception) -> str:
    return (
        "⚠️ Kanalga post qilib bo'lmadi.\n"
        f"Sabab: {str(e)[:200]}\n\n"
        "Tekshiring: bot shu kanalda admin va \"Xabar yuborish\" huquqi bormi."
    )


def channel_pick_kb(make_cb):
    rows = get_channels()
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📢 {title}", callback_data=make_cb(cid))] for cid, _chat, title in rows
    ])


# ============ KLAVIATURALAR ============
# Telegram tugmalarga faqat 3 ta rang beradi: "primary" (ko'k), "success" (yashil), "danger" (qizil).
# Sariq va oq rang YO'Q: rang berilmagan tugma Telegramning standart ko'rinishida chiqadi.
# Agar tugma ranglari ishlamasa (eski Telegram/aiogram), muhitga BTN_STYLES=0 qo'ying.
BTN_STYLES_ENABLED = os.environ.get("BTN_STYLES", "1") != "0"


def ibtn(text: str, cb: str = None, url: str = None, style: str = None) -> InlineKeyboardButton:
    kw = {"text": text}
    if url:
        kw["url"] = url
    else:
        kw["callback_data"] = cb or "noop"
    if style and BTN_STYLES_ENABLED:
        kw["style"] = style
    return InlineKeyboardButton(**kw)


def sbtn(text: str, style: str = None) -> InlineKeyboardButton:
    """Bosilganda foydalanuvchi chat yozish maydoniga "@bot " qo'yadi (inline qidiruvni boshlaydi)."""
    kw = {"text": text, "switch_inline_query_current_chat": ""}
    if style and BTN_STYLES_ENABLED:
        kw["style"] = style
    return InlineKeyboardButton(**kw)


def kbtn(text: str, style: str = None) -> KeyboardButton:
    kw = {"text": text}
    if style and BTN_STYLES_ENABLED:
        kw["style"] = style
    return KeyboardButton(**kw)


def esc(s) -> str:
    return _html.escape(str(s if s is not None else ""), quote=False)


def quote(html_text: str) -> str:
    """Captionni iqtibos (blockquote) ko'rinishiga o'rab beradi. Ichida iqtibos bo'lsa, qayta o'ramaydi."""
    if "<blockquote" in html_text:
        return html_text
    return f"<blockquote>{html_text}</blockquote>"


def quote_blocks(text: str) -> str:
    """Oddiy matnni HTML iqtiboslarga aylantiradi: bo'sh qator bilan ajratilgan har bir blok
    ALOHIDA iqtibos bo'ladi, blok ichidagi qatorlar bitta iqtibosda qoladi."""
    text = (text or "").replace("\r", "")
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    return "\n".join(f"<blockquote>{esc(b)}</blockquote>" for b in blocks)


def fit_quotes(text: str, limit: int = 1024) -> str:
    """Matnni limitga sig'dirib (kerak bo'lsa qisqartirib), iqtibos HTML qaytaradi."""
    text = (text or "").strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return quote_blocks(text)


def visible_len(html_text: str) -> int:
    return len(_html.unescape(re.sub(r"<[^>]+>", "", html_text or "")))


def nav_row(back_cb: str, page: int, total: int, fwd_cb: str):
    """Orqaga (qizil) | sahifa (ko'k) | Oldinga (yashil)."""
    return [
        ibtn("🔙 Orqaga", back_cb, style="danger"),
        ibtn(f"👀 {page}/{total}", "noop", style="primary"),
        ibtn("Oldinga 🔜", fwd_cb, style="success"),
    ]


async def send_rich(chat_id, photo_file_id, html_text: str, kb=None):
    """Rasm + iqtibosli caption. Caption 1024 dan uzun bo'lsa, rasm va matn alohida yuboriladi."""
    if photo_file_id:
        if visible_len(html_text) <= 1024:
            return await bot.send_photo(chat_id, photo_file_id, caption=html_text,
                                        parse_mode="HTML", reply_markup=kb)
        await bot.send_photo(chat_id, photo_file_id)
    return await bot.send_message(chat_id, html_text, parse_mode="HTML", reply_markup=kb)


# ---------- ADMIN PANEL (reply klaviatura) ----------
BTN_BACK = "🔙 Orqaga"
BTN_SEC_ADMIN = "👑 Admin/👤 Foydalanuvchi"
BTN_SEC_ANIME = "📥 Anime qo'sh/📤 olish"
BTN_SEC_PARAMS = "⚙️ Parametrlar"
BTN_ADMIN_ADD = "➕ Admin qo'shish"
BTN_ADMIN_DEL = "➖ Admin olish"
BTN_ADMIN_LIST = "📋 Adminlar ro'yxati"
BTN_USERS_STAT = "👥 Foydalanuvchilar statistikasi"
BTN_VIP_TAG = "📔 VIP tagi"
BTN_BROADCAST = "✈️ Xabar"
BTN_NEWS = "📒 Anime news"
BTN_NEWS_DEL = "🚫 News delete"
BTN_HOME_TAG = "✏️ Bosh sahifa tagi"
BTN_TAGS = "✏️ Taglarni tahrirlash"
TAG_KEYS = ("ep", "sub", "fav", "later", "profile")


def admin_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [kbtn(BTN_SEC_ADMIN, "primary")],
            [kbtn(BTN_SEC_ANIME, "success")],
            [kbtn(BTN_SEC_PARAMS, "danger")],
            [kbtn(BTN_TAGS, "primary")],
        ],
        resize_keyboard=True,
    )


def admin_users_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [kbtn(BTN_ADMIN_ADD, "success"), kbtn(BTN_ADMIN_DEL, "danger")],
            [kbtn(BTN_ADMIN_LIST, "primary")],
            [kbtn(BTN_USERS_STAT, "primary")],
            [kbtn(BTN_BACK)],
        ],
        resize_keyboard=True,
    )


def admin_anime_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [kbtn("📥 Anime qo'shish", "success"), kbtn("📤 Anime olish", "danger")],
            [kbtn("📥 Qism qo'shish", "success"), kbtn("📤 Qism olish", "danger")],
            [kbtn("📥 Fasl qo'shish", "success"), kbtn("📤 Fasl olish", "danger")],
            [kbtn("🔢 Kodni o'zgartirish", "primary"), kbtn("🔢 Kodlar ro'yxati", "primary")],
            [kbtn("👀 Qism post qilish", "primary"), kbtn("📣 Reklama", "primary")],
            [kbtn(BTN_BACK)],
        ],
        resize_keyboard=True,
    )


def admin_params_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [kbtn("📋 VIPlar ro'yxati", "primary")],
            [kbtn("💎 VIP berish", "success"), kbtn("🚫 VIP olish", "danger")],
            [kbtn("🔒 Majburiy obuna", "primary")],
            [kbtn("📢 Kanal reklama", "primary"), kbtn(BTN_BROADCAST, "success")],
            [kbtn(BTN_NEWS, "success"), kbtn(BTN_NEWS_DEL, "danger")],
            [kbtn(BTN_BACK)],
        ],
        resize_keyboard=True,
    )


# ---------- FOYDALANUVCHI (inline klaviatura) ----------
def seasons_keyboard(code: str, seasons) -> InlineKeyboardMarkup:
    buttons, row = [], []
    for sn in seasons:
        row.append(ibtn(f"🎬 {sn}-fasl", f"season:{code}:{sn}", style="primary"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def ep_numbers(code: str, season: int):
    rows = db("SELECT episode_number FROM episodes WHERE anime_code=? AND season_number=? "
              "ORDER BY episode_number", (code, season), "all")
    return [r[0] for r in rows]


def in_list(user_id: int, code: str, kind: str) -> bool:
    return bool(db("SELECT 1 FROM user_lists WHERE user_id=? AND anime_code=? AND list_type=?",
                   (user_id, code, kind), "one"))


def toggle_list(user_id: int, code: str, kind: str) -> bool:
    """Ro'yxatga qo'shsa True, olib tashlasa False qaytaradi."""
    if in_list(user_id, code, kind):
        db("DELETE FROM user_lists WHERE user_id=? AND anime_code=? AND list_type=?",
           (user_id, code, kind))
        return False
    db("INSERT OR REPLACE INTO user_lists (user_id, anime_code, list_type, added_date) VALUES (?,?,?,?)",
       (user_id, code, kind, datetime.now().isoformat()))
    return True


def build_ep_kb(user_id: int, code: str, season: int, cur: int = None, page: int = None) -> InlineKeyboardMarkup:
    """Qismlar tugmalari (yashil, 10 tadan bo'limli) + Orqaga/sahifa/Oldinga + Obuna/Yoqqan/Keyinroq."""
    numbers = ep_numbers(code, season)
    total_pages = max(1, (len(numbers) + EP_PAGE_SIZE - 1) // EP_PAGE_SIZE)
    if page is None:
        page = (numbers.index(cur) // EP_PAGE_SIZE + 1) if cur in numbers else 1
    page = max(1, min(page, total_pages))
    chunk = numbers[(page - 1) * EP_PAGE_SIZE: page * EP_PAGE_SIZE]
    cur_n = cur or 0

    rows, row = [], []
    for n in chunk:
        label = f"👀{n}" if n == cur else f"📀{n}"
        row.append(ibtn(label, f"ep:{code}:{season}:{n}", style="success"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    # 1-bo'limda "Orqaga" fasllar (anime kartochkasi) ga qaytaradi
    back_cb = f"epp:{code}:{season}:{page - 1}:{cur_n}" if page > 1 else f"seasons:{code}"
    fwd_cb = f"epp:{code}:{season}:{page + 1}:{cur_n}" if page < total_pages else "end"
    rows.append(nav_row(back_cb, page, total_pages, fwd_cb))

    sub = in_list(user_id, code, "sub")
    fav = in_list(user_id, code, "fav")
    later = in_list(user_id, code, "later")
    tail = f"{season}:{cur_n}:{page}"
    rows.append([
        ibtn("✅ Obuna" if sub else "❤️ Obuna", f"ul:s:{code}:{tail}", style="danger"),
        ibtn("✅ Yoqqan" if fav else "⭐️ Yoqqan", f"ul:f:{code}:{tail}"),
        ibtn("✅ Keyinroq" if later else "🕝 Keyinroq", f"ul:l:{code}:{tail}"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


LIST_KINDS = {
    "sub": ("❤️ Obunalarim", "😊Hurmatli foydalanuvchi bu bo'lim siz obuna bo'lgan animelar bo'limidir📂"),
    "fav": ("⭐️ Yoqqanlar", "😊Hurmatli foydalanuvchi bu bo'lim sizga yoqqan animelar bo'limidir📂"),
    "later": ("🕝 Keyinroq", "👀Bu bo'lim siz keyinroq🕝 ko'rmoqchi bo'lgan animelaringiz📂 bo'limidir"),
}
LIST_SHORT = {"s": "sub", "f": "fav", "l": "later"}


def count_list(user_id: int, kind: str) -> int:
    return db("SELECT COUNT(*) FROM user_lists u JOIN animes a ON a.code=u.anime_code "
              "WHERE u.user_id=? AND u.list_type=?", (user_id, kind), "one")[0]


def list_view(user_id: int, kind: str, page: int):
    """Obunalarim / Yoqqanlar / Keyinroq: 10 tadan bo'limli ro'yxat. (matn, klaviatura) qaytaradi."""
    _title, caption = LIST_KINDS[kind]
    rows = db("SELECT a.code, a.title FROM user_lists u JOIN animes a ON a.code=u.anime_code "
              "WHERE u.user_id=? AND u.list_type=? ORDER BY u.added_date DESC", (user_id, kind), "all")
    total_pages = max(1, (len(rows) + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE)
    page = max(1, min(page, total_pages))
    chunk = rows[(page - 1) * LIST_PAGE_SIZE: page * LIST_PAGE_SIZE]

    text = quote(tag_text(kind))
    if not rows:
        text += "\n\n📭 Hozircha bu bo'limda animelar yo'q."
    buttons = [[ibtn(f"🎬 {t}"[:60], f"lo:{c}", style="success")] for c, t in chunk]
    # 1-bo'limda "Orqaga" profilga qaytaradi, boshqa bo'limlarda oldingi bo'limga
    back_cb = f"pl:{kind}:{page - 1}" if page > 1 else "prof"
    fwd_cb = f"pl:{kind}:{page + 1}" if page < total_pages else "end"
    buttons.append(nav_row(back_cb, page, total_pages, fwd_cb))
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


# ============ MAJBURIY OBUNA ============
async def check_force_sub(user_id: int):
    channels = db("SELECT channel_id, display_name, join_url FROM force_sub_channels", (), "all")
    not_subscribed = []
    for ch_id, display_name, join_url in channels:
        try:
            member = await bot.get_chat_member(ch_id, user_id)
            if member.status in ("left", "kicked"):
                not_subscribed.append((ch_id, display_name, join_url))
        except TelegramBadRequest:
            not_subscribed.append((ch_id, display_name, join_url))
    return not_subscribed


def force_sub_keyboard(channels) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text="✅️Obuna bo'lish✅️", url=join_url)]
               for (_, _, join_url) in channels]
    buttons.append([InlineKeyboardButton(text="🔄 Tekshirish", callback_data="check_sub")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def needs_force_sub(user_id: int) -> bool:
    return not is_admin(user_id) and not is_vip(user_id)


# ============ STATISTIKA ============
def compute_dashboard_stats():
    total_animes = db("SELECT COUNT(*) FROM animes", (), "one")[0]
    total_episodes = db("SELECT COUNT(*) FROM episodes", (), "one")[0]
    total_users = db("SELECT COUNT(*) FROM users", (), "one")[0]
    cutoff = (datetime.now() - timedelta(minutes=ONLINE_WINDOW_MIN)).isoformat()
    online = db("SELECT COUNT(*) FROM users WHERE last_seen >= ?", (cutoff,), "one")[0]
    return total_animes, total_episodes, total_users, online


WELCOME_TEXT = (
    "An1zen botga hush kelibsiz👋\n\n"
    "Siz bu botda eng yangi va sifatli 💾\n"
    "Dublajdagi animelarni tomosha qila\n"
    "Olasiz👀\n\n"
    "Animelarni bu usul orqali topib olishingiz mumkin👇\n\n"
    "1. Animeni nomi bilan: Ya'ni botga startni bosgandan so'ng o'zingiz yoqtirgan "
    "anime nomini jo'natasiz va bot sizga o'sha animeni chiqarib beradi✏️\n\n"
    "An1zen jamoasi sizga maroqli hordiq tilab qoladi😊"
)

# Admin tahrirlay oladigan matnlar (📔 VIP tagi) — standart qiymatlari:
DEFAULT_VIP_CAPTION = (
    "😊Hurmatli foydalanuvchi siz👤\n"
    "Bu bo'limda📂 AniZen.uz animelar Botidan 💎Vip olishingiz mumkin🤗\n"
    "Eslatma‼️O'zingizga qulay vip tarifini tanlab berilgan Kartaga pul💵 tushirganingizdan so'ng "
    "adminga to'lov Chekini📃 @an1zen adminiga yuboring📤 va admin to'lovni qabul qilgandan so'ng📥 "
    "Sizga Botda Vip Beradi😊 Bu bir necha daqiqa Vaqt🕝 olishi mumkin ‼️"
)
DEFAULT_CARD_TEXT = (
    f"💳Karta raqami: <code>{PAYMENT_CARD}</code>\n"
    f"👤Karta egasi: {PAYMENT_CARD_OWNER}\n"
    f"📥Murojat uchun: {ADMIN_USERNAME}\n\n"
    "Diqqat‼️O'zingizga mos tarifni tanlaganingizdan so'ng 📥chekni botning o'ziga yuboring 📃"
)


def get_vip_text(key: str, default: str) -> str:
    v = get_setting(key, "")
    return v if v and v.strip() else default


# Foydalanuvchi qaysi VIP tarifni tanlagani (chek yuborguncha eslab turiladi)
PENDING_PLAN = {}
PENDING_PLAN_TTL_MIN = 180


def set_pending_plan(user_id: int, plan_key: str):
    PENDING_PLAN[user_id] = (plan_key, datetime.now())


def get_pending_plan(user_id: int):
    item = PENDING_PLAN.get(user_id)
    if not item:
        return None
    key, ts = item
    if datetime.now() - ts > timedelta(minutes=PENDING_PLAN_TTL_MIN):
        PENDING_PLAN.pop(user_id, None)
        return None
    return key


# ---------- Ekranlar (matn + tugmalar) ----------
PROFILE_HEAD = "🤗Assalamu alaykum. Hurmatli foydalanuvchi👤. Siz bu bo'limda o'zingizning ma'lumotlaringizni ko'ra olasiz👀"

TAG_LABELS = {
    "anime": "🎬 Anime tagi",
    "vip": "💎 VIP tagi",
    "ep": "📀 Qism tagi",
    "home": "🏠 Bosh sahifa tagi",
    "sub": "❤️ Obunalarim tagi",
    "fav": "⭐️ Yoqqanlar tagi",
    "later": "🕝 Keyinroq tagi",
    "profile": "👤 Profil tagi",
}


def tag_default(key: str) -> str:
    if key in LIST_KINDS:
        return LIST_KINDS[key][1]
    if key == "profile":
        return PROFILE_HEAD
    if key == "ep":
        return "🎬 {nomi}\n🎞 {fasl}-fasl, {qism}-qism"
    return ""


def tag_text(key: str) -> str:
    """Admin tahrirlagan tag bo'lsa o'shani, bo'lmasa standart matnni qaytaradi."""
    v = get_setting(f"tag:{key}", "")
    return v if v and v.strip() else tag_default(key)


def ep_default_caption(title, season, num) -> str:
    """Qism tagi shabloni: {nomi}, {fasl}, {qism} o'rniga haqiqiy qiymatlar qo'yiladi (oddiy matn)."""
    return (tag_text("ep").replace("{nomi}", str(title))
            .replace("{fasl}", str(season)).replace("{qism}", str(num)))


def tag_preview_html(key: str, value: str = None) -> str:
    if key == "ep":
        tpl = value if value is not None else tag_text("ep")
        sample = tpl.replace("{nomi}", "Namuna anime").replace("{fasl}", "1").replace("{qism}", "1")
        return fit_quotes(sample, 1024)
    return quote(value if value is not None else tag_text(key))


def home_html() -> str:
    """Bosh sahifa matni: admin tahrirlagan bo'lsa o'shani, bo'lmasa standart matnni iqtibosda chiqaradi."""
    custom = get_setting("home_caption", "")
    return quote(custom) if custom.strip() else quote(esc(WELCOME_TEXT))


def main_view():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [ibtn("👤 Profil", "prof", style="primary"), ibtn("💎 VIP olish", "vip", style="primary")],
        [sbtn("🔍 Qidiruv", style="success"),
         ibtn("‼️Murojat uchun", url=f"tg://user?id={CONTACT_USER_ID}", style="primary")],
        [ibtn(f"👀 {CHANNEL_USERNAME}", url=CHANNEL_URL), ibtn("📒 Anime news", "news", style="success")],
    ])
    return home_html(), kb


def profile_view(user_id: int):
    vip_status = "❌ Yo'q"
    if is_vip(user_id):
        row = db("SELECT vip_until FROM users WHERE user_id=?", (user_id,), "one")
        vip_status = "♾ Cheksiz" if (row and row[0] == "forever") else f"✅ {row[0][:10]} gacha"
    text = quote(
        tag_text("profile") + "\n\n"
        f"💎 VIP holati: {vip_status}\n"
        f"❤️ Obunalarim: {count_list(user_id, 'sub')} ta\n"
        f"⭐️ Yoqqanlar: {count_list(user_id, 'fav')} ta\n"
        f"🕝 Keyinroq: {count_list(user_id, 'later')} ta"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [ibtn("❤️ Obunalarim", "pl:sub:1", style="danger")],
        [ibtn("⭐️ Yoqqanlar", "pl:fav:1")],
        [ibtn("🕝 Keyinroq", "pl:later:1")],
        [ibtn("🔙 Orqaga", "home", style="danger")],
    ])
    return text, kb


def vip_view():
    caption = get_vip_text("vip_caption", DEFAULT_VIP_CAPTION)
    rows = [[ibtn(f"💵{label} {price}", f"vipplan:{key}", style="success")] for key, label, price in VIP_PLANS]
    rows.append([ibtn("🔙 Orqaga", "home", style="danger")])
    return quote(caption), InlineKeyboardMarkup(inline_keyboard=rows)


def plan_view(plan_key: str):
    plan = next((p for p in VIP_PLANS if p[0] == plan_key), None)
    if not plan:
        return None
    _key, label, price = plan
    card = get_vip_text("vip_card_text", DEFAULT_CARD_TEXT)
    text = quote(f"💎 Tanlangan tarif: <b>{label} — {price}</b>\n\n{card}")
    kb = InlineKeyboardMarkup(inline_keyboard=[[ibtn("🔙 Orqaga", "vip", style="danger")]])
    return text, kb


async def send_dashboard(message: Message):
    text, kb = main_view()
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


async def clear_reply_keyboard(message: Message):
    """Oddiy foydalanuvchida eski (admin) klaviatura qolib ketmasligi uchun, ko'rinmas tarzda tozalaydi."""
    try:
        tmp = await message.answer("⏳", reply_markup=ReplyKeyboardRemove())
        await tmp.delete()
    except Exception:
        pass


async def edit_view(callback: CallbackQuery, text: str, kb, prefer_text: bool = False):
    """Bosilgan xabarni joyida yangi caption va tugmalarga almashtiradi (eskisi yo'qoladi).
    prefer_text=True: xabar rasm/video bo'lsa, o'chirib, matnli yangi ekran yuboriladi."""
    msg = callback.message
    if prefer_text and msg.text is None:
        try:
            await msg.delete()
        except Exception:
            pass
        await msg.answer(text, parse_mode="HTML", reply_markup=kb)
        return
    try:
        if msg.text is not None:
            await msg.edit_text(text, parse_mode="HTML", reply_markup=kb)
        else:
            await msg.edit_caption(caption=text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest as e:
        if "not modified" in str(e).lower():
            return
        try:
            await msg.delete()
        except Exception:
            pass
        await msg.answer(text, parse_mode="HTML", reply_markup=kb)


# ============ ANIME KARTOCHKASI ============
LOCK_HTML = "\n\n💎 <b>Bu — faqat VIP foydalanuvchilar uchun anime!</b>\n/start bosib, 💎 VIP olish bo'limidan VIP oling."
LOCK_PLAIN = "\n\n💎 Bu — faqat VIP foydalanuvchilar uchun anime!\n/start bosib, 💎 VIP olish bo'limidan VIP oling."


def build_anime_card(code: str, user_id: int, bump: bool = True):
    """(poster_file_id, caption, parse_mode, keyboard) yoki None."""
    anime = db("SELECT code, title, total_seasons, total_episodes_declared, genre, channel_name, "
               "quality, rating, views, downloads, is_premium, poster_file_id, added_date, custom_caption "
               "FROM animes WHERE code=?", (code,), "one")
    if not anime:
        return None

    (acode, title, total_seasons, total_episodes_declared, genre, channel_name, quality, rating,
     views, downloads, is_premium, poster_file_id, added_date, custom_caption) = anime

    if bump:
        db("UPDATE animes SET views = views + 1 WHERE code=?", (acode,))
        views += 1
    season_list = get_seasons(acode)
    total_seasons = len(season_list)
    display_channel = channel_name or get_setting("ad_channel", "")

    caption = (
        f"🎬 <b>{esc(title)}</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"🎞 Fasllar soni: {total_seasons}\n"
    )
    ep_count = count_episodes(acode)
    if ep_count:
        caption += f"📀 Qismi: {ep_count}\n"
    caption += f"🏷 Janri: {esc(genre)}\n"
    if display_channel:
        caption += f"📢 Kanal: {esc(display_channel)}\n"
    caption += (
        "━━━━━━━━━━━━━━━\n"
        f"👀 Ko'rilgan: {views} marta\n"
        f"⬇️ Yuklab olingan: {downloads} marta"
    )

    locked = bool(is_premium) and not is_vip(user_id)
    if locked:
        caption += LOCK_HTML

    kb = None
    if not locked and season_list:
        kb = seasons_keyboard(acode, season_list)

    parse_mode = "HTML"
    if custom_caption:
        # Admin yozgan tag: bo'sh qator bilan ajratilgan har bir blok alohida iqtibosda chiqadi
        suffix_len = len(LOCK_PLAIN) if locked else 0
        caption = fit_quotes(render_qismi(custom_caption, count_episodes(acode)), 1024 - suffix_len)
        if locked:
            caption += LOCK_HTML
    return poster_file_id, caption, parse_mode, kb


async def show_anime(message: Message, code: str, user_id: int):
    card = build_anime_card(code, user_id)
    if not card:
        await message.answer("❌ Bunday kodli/nomli anime topilmadi.")
        return
    poster_file_id, caption, parse_mode, kb = card
    try:
        if poster_file_id:
            await message.answer_photo(poster_file_id, caption=caption, parse_mode=parse_mode, reply_markup=kb)
        else:
            await message.answer(caption, parse_mode=parse_mode, reply_markup=kb)
    except TelegramBadRequest as e:
        logging.error(e)
        await message.answer(caption, parse_mode=parse_mode, reply_markup=kb)


async def show_card_in_place(msg: Message, code: str, user_id: int):
    """Video/qismlar xabarini joyida anime kartochkasiga (poster + fasllar) qaytaradi."""
    card = build_anime_card(code, user_id, bump=False)
    if not card:
        await msg.answer("❌ Bunday kodli/nomli anime topilmadi.")
        return
    poster_file_id, caption, parse_mode, kb = card
    try:
        if poster_file_id and msg.text is None:
            await msg.edit_media(InputMediaPhoto(media=poster_file_id, caption=caption, parse_mode=parse_mode),
                                 reply_markup=kb)
            return
        if not poster_file_id and msg.text is not None:
            await msg.edit_text(caption, parse_mode=parse_mode, reply_markup=kb)
            return
    except TelegramBadRequest as e:
        if "not modified" in str(e).lower():
            return
        logging.info(f"show_card_in_place: {e}")
    try:
        await msg.delete()
    except Exception:
        pass
    await show_anime(msg, code, user_id)


# ============ QISM (VIDEO) ============
def episode_payload(user_id: int, code: str, season: int, num: int):
    """(xato_matni | None, file_id, caption)"""
    anime = db("SELECT is_premium, title FROM animes WHERE code=?", (code,), "one")
    if not anime:
        return "❌ Bunday anime topilmadi.", None, None
    if anime[0] and not is_vip(user_id):
        return "💎 Bu qism faqat VIP uchun! /start bosib, 💎 VIP olish bo'limidan VIP oling.", None, None
    ep = db("SELECT file_id, episode_title FROM episodes WHERE anime_code=? AND season_number=? AND episode_number=?",
            (code, season, num), "one")
    if not ep:
        return "❌ Bu qism hali yuklanmagan.", None, None
    caption = ep[1] or ep_default_caption(anime[1], season, num)
    return None, ep[0], fit_quotes(caption, 1024)


def mark_watched(user_id: int, code: str, season: int, num: int):
    db("UPDATE animes SET downloads = downloads + 1 WHERE code=?", (code,))
    db("INSERT OR REPLACE INTO history (user_id, anime_code, season_number, episode_number, watched_date) VALUES (?,?,?,?,?)",
       (user_id, code, season, num, datetime.now().isoformat()))


async def send_episode(message: Message, user_id: int, code: str, season: int, num: int):
    err, file_id, caption = episode_payload(user_id, code, season, num)
    if err:
        await message.answer(err)
        return
    await bot.send_video(message.chat.id, file_id, caption=caption, parse_mode="HTML",
                         reply_markup=build_ep_kb(user_id, code, season, cur=num))
    mark_watched(user_id, code, season, num)


# ============ /START ============
@router.message(Command("start"))
async def cmd_start(message: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    ensure_user(message.from_user.id, message.from_user.username)
    admin = is_admin(message.from_user.id)

    not_subscribed = await check_force_sub(message.from_user.id) if needs_force_sub(message.from_user.id) else []
    if not_subscribed:
        await message.answer(
            "📢 Botdan foydalanish uchun quyidagi kanal(lar)ga obuna bo'ling:",
            reply_markup=force_sub_keyboard(not_subscribed),
        )
        return

    if command.args and command.args.strip().lower() == "news":
        if admin:
            await message.answer("🛠 Admin panel faol.", reply_markup=admin_menu())
        text, kb = news_dates_view(1)
        await message.answer(text, parse_mode="HTML", reply_markup=kb)
        return

    if command.args:
        arg = command.args.strip()
        parts = arg.split("_")
        if len(parts) == 3:
            code, season, num = parts
            try:
                season, num = int(season), int(num)
            except ValueError:
                code = arg
                season = num = None
        else:
            code, season, num = arg, None, None

        if db("SELECT 1 FROM animes WHERE code=?", (code,), "one"):
            if admin:
                await message.answer("🛠 Admin panel faol.", reply_markup=admin_menu())
            await show_anime(message, code, message.from_user.id)
            if season and num:
                await send_episode(message, message.from_user.id, code, season, num)
            return

    if admin:
        await message.answer("🛠 Admin panel faol.", reply_markup=admin_menu())
    else:
        await clear_reply_keyboard(message)
    await send_dashboard(message)


@router.callback_query(F.data == "check_sub")
async def cb_check_sub(callback: CallbackQuery):
    not_subscribed = await check_force_sub(callback.from_user.id)
    if not_subscribed:
        await callback.answer("❗️ Hali barcha kanallarga obuna bo'lmadingiz!", show_alert=True)
        return
    await callback.message.answer("✅ Rahmat! Endi anime qidirishingiz mumkin.")
    await send_dashboard(callback.message)
    await callback.answer()


# ============ 🔍 INLINE QIDIRUV (@bot anime nomi) ============
INLINE_PAGE = 49  # Telegram bir javobda ko'pi bilan 50 natija beradi; 1 tasi holat qatori uchun

POSTER_BUSY = set()
POSTER_FAILED = {}  # kod -> oxirgi xato vaqti (xato bo'lsa 10 daqiqa qayta urinilmaydi)


def telegraph_url_from_response(data):
    if isinstance(data, list) and data and isinstance(data[0], dict) and data[0].get("src"):
        return "https://telegra.ph" + data[0]["src"]
    return None


async def upload_to_telegraph(file_id: str):
    """Posterni Telegram'dan olib, telegra.ph ga yuklaydi va ochiq havolasini qaytaradi."""
    f = await bot.get_file(file_id)
    buf = io.BytesIO()
    await bot.download_file(f.file_path, destination=buf)
    buf.seek(0)
    form = aiohttp.FormData()
    form.add_field("file", buf, filename="poster.jpg", content_type="image/jpeg")
    async with aiohttp.ClientSession() as session:
        async with session.post("https://telegra.ph/upload", data=form,
                                timeout=aiohttp.ClientTimeout(total=30)) as resp:
            data = await resp.json(content_type=None)
    return telegraph_url_from_response(data)


async def ensure_poster_url(code: str, file_id: str):
    """Poster ochiq havolasini bir marta olib, animes jadvaliga saqlaydi (inline natijada tag bilan chiqishi uchun)."""
    if not file_id or code in POSTER_BUSY:
        return
    failed_at = POSTER_FAILED.get(code)
    if failed_at and datetime.now() - failed_at < timedelta(minutes=10):
        return
    POSTER_BUSY.add(code)
    try:
        url = await upload_to_telegraph(file_id)
        if url:
            db("UPDATE animes SET poster_url=? WHERE code=?", (url, code))
            POSTER_FAILED.pop(code, None)
        else:
            POSTER_FAILED[code] = datetime.now()
    except Exception as e:
        logging.error(f"poster yuklanmadi ({code}): {e}")
        POSTER_FAILED[code] = datetime.now()
    finally:
        POSTER_BUSY.discard(code)


async def backfill_posters():
    """Bot ishga tushganda, havolasi yo'q animelarning posterlarini fonda yuklaydi."""
    rows = db("SELECT code, poster_file_id FROM animes WHERE poster_url IS NULL AND poster_file_id IS NOT NULL",
              (), "all")
    for code, fid in rows:
        await ensure_poster_url(code, fid)
        await asyncio.sleep(1)


def inline_caption(code: str, title: str, genre: str, custom: str) -> str:
    """Anime tagi (HTML). Admin yozgan tag bo'lsa, o'shani iqtibosda ko'rsatadi."""
    if custom:
        return fit_quotes(render_qismi(custom, count_episodes(code)), 1000)
    lines = [f"🎬 <b>{esc(title)}</b>", f"🎞 Fasllar soni: {len(get_seasons(code))}"]
    ep = count_episodes(code)
    if ep:
        lines.append(f"📀 Qismi: {ep}")
    if genre and genre != "-":
        lines.append(f"🏷 Janri: {esc(genre)}")
    return quote("\n".join(lines))


def inline_summary(code: str, genre: str, custom: str) -> str:
    """Qidiruv ro'yxatida nom ostida chiqadigan qisqa tag (oddiy matn)."""
    if custom:
        text = render_qismi(custom, count_episodes(code))
        text = " • ".join(ln.strip() for ln in text.split("\n") if ln.strip())
    else:
        parts = [f"{len(get_seasons(code))} fasl", f"{count_episodes(code)} qism"]
        if genre and genre != "-":
            parts.append(genre)
        text = " • ".join(parts)
    return text[:200]


def inline_anime_rows(q: str, offset: int):
    cols = "code, title, genre, custom_caption, poster_file_id, poster_url FROM animes"
    if q:
        return db(f"SELECT {cols} WHERE title LIKE ? "
                  "ORDER BY CASE WHEN lower(title)=lower(?) THEN 0 ELSE 1 END, title LIMIT ? OFFSET ?",
                  (f"%{q}%", q, INLINE_PAGE, offset), "all")
    return db(f"SELECT {cols} ORDER BY added_date DESC LIMIT ? OFFSET ?", (INLINE_PAGE, offset), "all")


async def _inline_search_impl(query: InlineQuery):
    ensure_user(query.from_user.id, query.from_user.username)
    q = " ".join((query.query or "").split())
    try:
        offset = max(0, int(query.offset or 0))
    except ValueError:
        offset = 0

    results = []
    if offset == 0:
        cutoff = (datetime.now() - timedelta(minutes=ONLINE_WINDOW_MIN)).isoformat()
        online = db("SELECT COUNT(*) FROM users WHERE last_seen >= ?", (cutoff,), "one")[0]
        total = db("SELECT COUNT(*) FROM animes", (), "one")[0]
        stat = f"🟢 {online} kishi online • ✨ {total} ta anime mavjud"
        results.append(InlineQueryResultArticle(
            id="hdr",
            title=stat,
            description="Anime nomini yozing 🔍",
            input_message_content=InputTextMessageContent(message_text=stat),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                ibtn("🤖 Botga o'tish", url=f"https://t.me/{BOT_USERNAME}", style="primary")
            ]]),
        ))

    rows = inline_anime_rows(q, offset)
    for code, title, genre, custom, poster_file_id, poster_url in rows:
        rid = f"a{code}"[:64]
        has_seasons = bool(get_seasons(code))
        if poster_url:
            # Ro'yxatda: rasm + nom + tag. Bosilganda: rasm (link preview) + tag + fasllar tugmasi
            results.append(InlineQueryResultArticle(
                id=rid, title=title[:100], description=inline_summary(code, genre, custom),
                thumbnail_url=poster_url,
                input_message_content=InputTextMessageContent(message_text=inline_card_text(code), parse_mode="HTML"),
                reply_markup=inline_main_kb(code, has_seasons),
            ))
        elif poster_file_id:
            # Havola hali tayyor emas: rasmli natija (poster fonda yuklanadi, keyingi safar tag chiqadi)
            asyncio.create_task(ensure_poster_url(code, poster_file_id))
            results.append(InlineQueryResultCachedPhoto(
                id=rid, photo_file_id=poster_file_id, title=title[:100],
                description=inline_summary(code, genre, custom),
                caption=inline_caption(code, title, genre, custom), parse_mode="HTML",
                reply_markup=inline_main_kb(code, False),
            ))
        else:
            results.append(InlineQueryResultArticle(
                id=rid, title=title[:100], description=inline_summary(code, genre, custom),
                input_message_content=InputTextMessageContent(message_text=inline_card_text(code), parse_mode="HTML"),
                reply_markup=inline_main_kb(code, has_seasons),
            ))

    next_off = str(offset + INLINE_PAGE) if len(rows) == INLINE_PAGE else ""
    await query.answer(results, cache_time=20, is_personal=False, next_offset=next_off)


@router.inline_query()
async def inline_search(query: InlineQuery):
    """Inline so'rov: logga yozadi, xato bo'lsa ham Telegramga javob beradi."""
    logging.info(f"inline so'rov: {query.query!r} (user {query.from_user.id})")
    try:
        await _inline_search_impl(query)
    except Exception as e:
        logging.exception(f"inline qidiruv xatosi: {e}")
        try:
            await query.answer([], cache_time=5, is_personal=True)
        except Exception:
            logging.exception("inline javob yuborilmadi")


def inline_card_text(code: str, extra: str = "") -> str:
    """Inline xabar matni: rasm havolasi (link preview) + tag + ixtiyoriy qo'shimcha qator."""
    row = db("SELECT title, genre, custom_caption, poster_url FROM animes WHERE code=?", (code,), "one")
    if not row:
        return ""
    title, genre, custom, poster_url = row
    text = (f'<a href="{poster_url}">\u200b</a>' if poster_url else "") + inline_caption(code, title, genre, custom)
    if extra:
        text += "\n" + esc(extra)
    return text


def inline_main_kb(code: str, with_seasons: bool = True) -> InlineKeyboardMarkup:
    rows = [[ibtn("🎬 Anime ko'rish", url=f"https://t.me/{BOT_USERNAME}?start={code}", style="success")]]
    if with_seasons:
        rows.append([ibtn("📺 Fasllar", f"ia:s:{code}", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def inline_seasons_kb(code: str, seasons) -> InlineKeyboardMarkup:
    rows, row = [], []
    for sn in seasons:
        row.append(ibtn(f"🎬 {sn}-fasl", f"ia:e:{code}:{sn}:1", style="primary"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([ibtn("🔙 Orqaga", f"ia:m:{code}", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def inline_eps_kb(code: str, season: int, page: int) -> InlineKeyboardMarkup:
    """Qism tugmalari bot havolasi: bosilganda bot ochilib o'sha qism chiqadi (video inline xabarga tushmaydi)."""
    numbers = ep_numbers(code, season)
    total = max(1, (len(numbers) + EP_PAGE_SIZE - 1) // EP_PAGE_SIZE)
    page = max(1, min(page, total))
    chunk = numbers[(page - 1) * EP_PAGE_SIZE: page * EP_PAGE_SIZE]
    rows, row = [], []
    for n in chunk:
        row.append(ibtn(f"📀{n}", url=f"https://t.me/{BOT_USERNAME}?start={code}_{season}_{n}", style="success"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    back_cb = f"ia:e:{code}:{season}:{page - 1}" if page > 1 else f"ia:s:{code}"
    fwd_cb = f"ia:e:{code}:{season}:{page + 1}" if page < total else "end"
    rows.append(nav_row(back_cb, page, total, fwd_cb))
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _edit_inline(callback: CallbackQuery, text: str, kb: InlineKeyboardMarkup):
    try:
        await bot.edit_message_text(text=text, inline_message_id=callback.inline_message_id,
                                    parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest as e:
        if "not modified" not in str(e).lower():
            logging.error(f"inline xabarni tahrirlab bo'lmadi: {e}")
    await callback.answer()


@router.callback_query(F.data.startswith("ia:"))
async def cb_inline_card(callback: CallbackQuery):
    """Inline natijadan chiqqan xabar ichida: fasllar va qismlar ro'yxati."""
    if not callback.inline_message_id:
        await callback.answer()
        return
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer()
        return
    action, code = parts[1], parts[2]
    row = db("SELECT is_premium FROM animes WHERE code=?", (code,), "one")
    if not row:
        await callback.answer("⚠️ Anime topilmadi.", show_alert=True)
        return
    if row[0] and not is_vip(callback.from_user.id):
        await callback.answer("💎 Bu anime faqat VIP foydalanuvchilar uchun!", show_alert=True)
        return
    try:
        if action == "m":
            text = inline_card_text(code)
            kb = inline_main_kb(code, bool(get_seasons(code)))
        elif action == "s":
            seasons = get_seasons(code)
            if not seasons:
                await callback.answer("❌ Fasllar hali yuklanmagan.", show_alert=True)
                return
            text = inline_card_text(code, "👇 Fasllardan birini tanlang")
            kb = inline_seasons_kb(code, seasons)
        elif action == "e":
            season, page = int(parts[3]), int(parts[4])
            text = inline_card_text(code, f"🎞 {season}-fasl qismlari. Qismni bossangiz bot ochilib, o'sha qism chiqadi.")
            kb = inline_eps_kb(code, season, page)
        else:
            await callback.answer()
            return
    except (ValueError, IndexError):
        await callback.answer()
        return
    await _edit_inline(callback, text, kb)


@router.chosen_inline_result()
async def inline_chosen(chosen: ChosenInlineResult):
    """Foydalanuvchi qidiruv natijasini bossa, logga yozadi (BotFather'da /setinlinefeedback yoqilgan bo'lishi kerak)."""
    logging.info(f"inline tanlandi: result_id={chosen.result_id!r} query={chosen.query!r} user={chosen.from_user.id}")


# ============ QIDIRISH ============
@router.message(StateFilter(SearchStates.waiting_query))
async def process_search(message: Message, state: FSMContext):
    await state.clear()
    ensure_user(message.from_user.id, message.from_user.username)
    if needs_force_sub(message.from_user.id):
        not_subscribed = await check_force_sub(message.from_user.id)
        if not_subscribed:
            await message.answer("📢 Avval kanal(lar)ga obuna bo'ling:", reply_markup=force_sub_keyboard(not_subscribed))
            return
    await _do_search(message, message.text.strip())


def clean_search_query(text: str) -> str:
    """'@an1zen_bot sehrli' -> 'sehrli'. Foydalanuvchi bot nomini qo'lda yozib yuborsa ham qidiruv ishlaydi."""
    t = (text or "").strip()
    if BOT_USERNAME:
        t = re.sub(r"^@" + re.escape(BOT_USERNAME) + r"\b\s*", "", t, flags=re.IGNORECASE)
    return t.strip()


async def _do_search(message: Message, query: str):
    query = clean_search_query(query)
    if not query:
        await message.answer("🔍 Anime nomini yozing (masalan: Naruto).\n"
                             "Yoki 🔍 Qidiruv tugmasini bosib, nomini yozing.")
        return
    matches = db("SELECT code, title FROM animes WHERE title LIKE ? "
                 "ORDER BY CASE WHEN lower(title)=lower(?) THEN 0 ELSE 1 END, title",
                 (f"%{query}%", query), "all")
    if not matches:
        await message.answer("❌ Hech narsa topilmadi.")
        return
    if len(matches) == 1:
        await show_anime(message, matches[0][0], message.from_user.id)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [ibtn(t, f"open:{c}", style="success")] for c, t in matches[:15]
    ])
    await message.answer("Bir nechta natija topildi, birini tanlang:", reply_markup=kb)


@router.callback_query(F.data.startswith("open:"))
async def cb_open(callback: CallbackQuery):
    code = callback.data.split(":")[1]
    await show_anime(callback.message, code, callback.from_user.id)
    await callback.answer()


@router.callback_query(F.data.startswith("season:"))
async def cb_season(callback: CallbackQuery):
    _, code, season = callback.data.split(":")
    season = int(season)
    exists = db("SELECT 1 FROM episodes WHERE anime_code=? AND season_number=?",
                (code, season), "one")
    if not exists:
        await callback.answer("❌ Bu faslda qismlar hali yuklanmagan.", show_alert=True)
        return
    row = db("SELECT title FROM animes WHERE code=?", (code,), "one")
    title = row[0] if row else code
    text = quote(f"🎬 <b>{esc(title)}</b>\n🎞 {season}-fasl qismlari:\n\n👇 Ko'rmoqchi bo'lgan qismni tanlang")
    await edit_view(callback, text, build_ep_kb(callback.from_user.id, code, season))
    await callback.answer()


@router.callback_query(F.data.startswith("seasons:"))
async def cb_seasons(callback: CallbackQuery):
    code = callback.data.split(":", 1)[1]
    if not get_seasons(code):
        await callback.answer("❌ Fasllar topilmadi.", show_alert=True)
        return
    await show_card_in_place(callback.message, code, callback.from_user.id)
    await callback.answer()


@router.callback_query(F.data.startswith("ep:"))
async def cb_episode(callback: CallbackQuery):
    """Qism tugmasi bosilganda video o'sha xabarning o'zida almashadi (eskisi yo'qoladi)."""
    _, code, season, num = callback.data.split(":")
    season, num = int(season), int(num)
    uid = callback.from_user.id
    ensure_user(uid, callback.from_user.username)
    err, file_id, caption = episode_payload(uid, code, season, num)
    if err:
        await callback.answer(err, show_alert=True)
        return
    kb = build_ep_kb(uid, code, season, cur=num)
    msg = callback.message
    try:
        await msg.edit_media(InputMediaVideo(media=file_id, caption=caption, parse_mode="HTML"), reply_markup=kb)
    except TelegramBadRequest as e:
        if "not modified" in str(e).lower():
            await callback.answer()
            return
        logging.info(f"edit_media bo'lmadi, yangi xabar yuboriladi: {e}")
        try:
            await msg.delete()
        except Exception:
            pass
        try:
            await bot.send_video(msg.chat.id, file_id, caption=caption, parse_mode="HTML", reply_markup=kb)
        except Exception as e2:
            logging.error(e2)
            await callback.answer("⚠️ Xatolik yuz berdi.", show_alert=True)
            return
    mark_watched(uid, code, season, num)
    await callback.answer()


@router.callback_query(F.data.startswith("epp:"))
async def cb_ep_page(callback: CallbackQuery):
    try:
        _, code, season, page, cur = callback.data.split(":")
        kb = build_ep_kb(callback.from_user.id, code, int(season), int(cur) or None, int(page))
    except ValueError:
        await callback.answer()
        return
    try:
        await callback.message.edit_reply_markup(reply_markup=kb)
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("ul:"))
async def cb_user_list_toggle(callback: CallbackQuery):
    """Qism ostidagi ❤️ Obuna / ⭐️ Yoqqan / 🕝 Keyinroq tugmalari."""
    try:
        _, short, code, season, cur, page = callback.data.split(":")
        season, cur, page = int(season), int(cur), int(page)
    except ValueError:
        await callback.answer()
        return
    kind = LIST_SHORT.get(short)
    if not kind or not db("SELECT 1 FROM animes WHERE code=?", (code,), "one"):
        await callback.answer("⚠️ Bajarilmadi.", show_alert=True)
        return
    uid = callback.from_user.id
    added = toggle_list(uid, code, kind)
    toast = {
        ("sub", True): "❤️ Obunalarim bo'limiga qo'shildi",
        ("sub", False): "💔 Obunalaringizdan olib tashlandi",
        ("fav", True): "⭐️ Yoqqanlar bo'limiga qo'shildi",
        ("fav", False): "Yoqqanlardan olib tashlandi",
        ("later", True): "🕝 Keyinroq bo'limiga qo'shildi",
        ("later", False): "Keyinroqdan olib tashlandi",
    }[(kind, added)]
    try:
        await callback.message.edit_reply_markup(reply_markup=build_ep_kb(uid, code, season, cur or None, page))
    except TelegramBadRequest:
        pass
    await callback.answer(toast)


@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data == "end")
async def cb_end(callback: CallbackQuery):
    await callback.answer("Bu oxirgi bo'lim 👀")


# ---------- Bosh sahifa / Profil / VIP ----------
@router.callback_query(F.data == "home")
async def cb_home(callback: CallbackQuery):
    text, kb = main_view()
    await edit_view(callback, text, kb)
    await callback.answer()


@router.callback_query(F.data == "prof")
async def cb_profile(callback: CallbackQuery):
    ensure_user(callback.from_user.id, callback.from_user.username)
    text, kb = profile_view(callback.from_user.id)
    await edit_view(callback, text, kb)
    await callback.answer()


@router.callback_query(F.data.startswith("pl:"))
async def cb_list_page(callback: CallbackQuery):
    try:
        _, kind, page = callback.data.split(":")
        page = int(page)
    except ValueError:
        await callback.answer()
        return
    if kind not in LIST_KINDS:
        await callback.answer()
        return
    text, kb = list_view(callback.from_user.id, kind, page)
    await edit_view(callback, text, kb)
    await callback.answer()


@router.callback_query(F.data.startswith("lo:"))
async def cb_list_open(callback: CallbackQuery):
    code = callback.data.split(":", 1)[1]
    try:
        await callback.message.delete()
    except Exception:
        pass
    await show_anime(callback.message, code, callback.from_user.id)
    await callback.answer()


@router.callback_query(F.data == "vip")
async def cb_vip(callback: CallbackQuery):
    text, kb = vip_view()
    await edit_view(callback, text, kb)
    await callback.answer()


@router.callback_query(F.data.startswith("vipplan:"))
async def cb_vip_plan(callback: CallbackQuery):
    key = callback.data.split(":", 1)[1]
    view = plan_view(key)
    if not view:
        await callback.answer("⚠️ Tarif topilmadi.", show_alert=True)
        return
    set_pending_plan(callback.from_user.id, key)
    text, kb = view
    await edit_view(callback, text, kb)
    await callback.answer("📃 Endi chekni shu botning o'ziga yuboring")


# ============ 📒 ANIME NEWS ============
NEWS_PAGE_SIZE = 10
NEWS_CAPTION = ("😊Hurmatli foydalanuvchi bu bo'limda siz chiqishi kutilayotgan animelarni "
                "qachon chiqishini bilib olishingiz mumkin bo'ladi👀")

_MONTH_PREFIXES = [
    ("yan", "jan"), ("fev", "feb"), ("mar",), ("apr",), ("may",), ("iyun", "iyn", "jun"),
    ("iyul", "iyl", "jul"), ("avg", "aug"), ("sen", "sep"), ("okt", "oct"), ("noy", "nov"), ("dek", "dec"),
]


def parse_news_date(label: str) -> int:
    """'1 okt', '10 oktyabr', '10.10' kabi yozuvdan saralash kalitini (YYYYMMDD) chiqaradi.
    Tushunmasa — eng oxiriga qo'yiladi."""
    t = (label or "").lower().replace("ʻ", "'").replace("’", "'")
    month = day = None
    m = re.search(r"(\d{1,2})\s*[-./ ]\s*(\d{1,2})(?!\d)", t)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
    else:
        m = re.search(r"(\d{1,2})\s*-?\s*([a-z']+)", t)
        if m:
            day, word = int(m.group(1)), m.group(2).replace("'", "")
            for i, prefixes in enumerate(_MONTH_PREFIXES, start=1):
                if any(word.startswith(p) for p in prefixes):
                    month = i
                    break
    if not month or not day:
        return 99999999
    today = datetime.now().date()
    try:
        d = datetime(today.year, month, day).date()
        if d < today - timedelta(days=45):
            d = datetime(today.year + 1, month, day).date()
    except ValueError:
        return 99999999
    return int(d.strftime("%Y%m%d"))


def news_dates():
    return db("SELECT id, label FROM news_dates ORDER BY sort_key, id", (), "all")


def news_items(date_id: int):
    return db("SELECT id, title FROM news_items WHERE date_id=? ORDER BY id", (date_id,), "all")


def news_post_html(label: str, body: str) -> str:
    return f"📒 <b>Anime news</b> · 📅 <b>{esc(label)}</b>\n\n{quote_blocks(body)}"


def news_dates_view(page: int):
    dates = news_dates()
    total = max(1, (len(dates) + NEWS_PAGE_SIZE - 1) // NEWS_PAGE_SIZE)
    page = max(1, min(page, total))
    chunk = dates[(page - 1) * NEWS_PAGE_SIZE: page * NEWS_PAGE_SIZE]
    text = quote(NEWS_CAPTION)
    if not dates:
        text += "\n\n📭 Hozircha chiqishi kutilayotgan animelar qo'shilmagan."
    rows, row = [], []
    for did, label in chunk:
        row.append(ibtn(f"📅 {label}"[:40], f"nd:{did}:1", style="primary"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    back_cb = f"nw:{page - 1}" if page > 1 else "home"
    fwd_cb = f"nw:{page + 1}" if page < total else "end"
    rows.append(nav_row(back_cb, page, total, fwd_cb))
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def news_items_view(date_id: int, page: int):
    d = db("SELECT label FROM news_dates WHERE id=?", (date_id,), "one")
    if not d:
        return None
    items = news_items(date_id)
    total = max(1, (len(items) + NEWS_PAGE_SIZE - 1) // NEWS_PAGE_SIZE)
    page = max(1, min(page, total))
    chunk = items[(page - 1) * NEWS_PAGE_SIZE: page * NEWS_PAGE_SIZE]
    text = quote(f"📅 <b>{esc(d[0])}</b> kuni chiqadigan animelar:\n\n"
                 "👇 Anime nomini tanlang, u haqida ma'lumot chiqadi")
    if not items:
        text += "\n\n📭 Bu kunga hali anime qo'shilmagan."
    rows = [[ibtn(f"🎬 {t}"[:60], f"ni:{i}", style="success")] for i, t in chunk]
    back_cb = f"nd:{date_id}:{page - 1}" if page > 1 else "nw:1"
    fwd_cb = f"nd:{date_id}:{page + 1}" if page < total else "end"
    rows.append(nav_row(back_cb, page, total, fwd_cb))
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("nw:"))
async def cb_news_dates(callback: CallbackQuery):
    try:
        page = int(callback.data.split(":")[1])
    except ValueError:
        page = 1
    text, kb = news_dates_view(page)
    await edit_view(callback, text, kb, prefer_text=True)
    await callback.answer()


@router.callback_query(F.data == "news")
async def cb_news_open(callback: CallbackQuery):
    text, kb = news_dates_view(1)
    await edit_view(callback, text, kb, prefer_text=True)
    await callback.answer()


@router.callback_query(F.data.startswith("nd:"))
async def cb_news_date(callback: CallbackQuery):
    try:
        _, did, page = callback.data.split(":")
        view = news_items_view(int(did), int(page))
    except ValueError:
        await callback.answer()
        return
    if not view:
        await callback.answer("⚠️ Bu kun olib tashlangan.", show_alert=True)
        return
    text, kb = view
    await edit_view(callback, text, kb, prefer_text=True)
    await callback.answer()


@router.callback_query(F.data.startswith("ni:"))
async def cb_news_item(callback: CallbackQuery):
    try:
        item_id = int(callback.data.split(":")[1])
    except ValueError:
        await callback.answer()
        return
    item = db("SELECT i.title, i.body, i.photo_file_id, i.date_id, d.label FROM news_items i "
              "JOIN news_dates d ON d.id=i.date_id WHERE i.id=?", (item_id,), "one")
    if not item:
        await callback.answer("⚠️ Bu anime news dan olib tashlangan.", show_alert=True)
        return
    _title, body, photo, date_id, label = item
    ids = [r[0] for r in news_items(date_id)]
    page = (ids.index(item_id) // NEWS_PAGE_SIZE + 1) if item_id in ids else 1
    kb = InlineKeyboardMarkup(inline_keyboard=[[ibtn("🔙 Orqaga", f"nd:{date_id}:{page}", style="danger")]])
    html_text = news_post_html(label, body)
    msg = callback.message
    if not photo:
        await edit_view(callback, html_text, kb)
    else:
        try:
            await msg.delete()
        except Exception:
            pass
        await send_rich(msg.chat.id, photo, html_text, kb)
    await callback.answer()


# ============ FOYDALANUVCHI BUYRUQLARI ============
@router.message(Command("vip"))
async def cmd_vip(message: Message, command: CommandObject):
    # Admin ishlatishi: /vip USER_ID MUDDAT  (masalan /vip 123456789 1oy yoki /vip 123456789 forever)
    if command.args and is_admin(message.from_user.id):
        parts = command.args.split()
        if len(parts) == 2:
            try:
                target_id = int(parts[0])
            except ValueError:
                await message.answer("❗️ Foydalanish: /vip USER_ID MUDDAT")
                return
            duration = parts[1]
            if duration != "forever" and duration not in MONTH_TO_DAYS:
                await message.answer("❗️ Muddat: 1hafta, 1oy, 3oy, 5oy, 7oy, 8oy yoki forever")
                return
            grant_vip(target_id, duration)
            await message.answer(f"✅ {target_id} foydalanuvchiga VIP berildi ({duration}).")
            try:
                await bot.send_message(target_id, "🎉 Tabriklaymiz! Sizga VIP faollashtirildi.")
            except Exception:
                pass
            return

    text, kb = vip_view()
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.message(Command("profil"))
async def cmd_profil(message: Message):
    ensure_user(message.from_user.id, message.from_user.username)
    text, kb = profile_view(message.from_user.id)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.message(Command("tarix"))
async def cmd_tarix(message: Message):
    ensure_user(message.from_user.id, message.from_user.username)
    rows = db("""SELECT a.title, h.anime_code, h.season_number, MAX(h.episode_number), MAX(h.watched_date)
                 FROM history h JOIN animes a ON a.code = h.anime_code
                 WHERE h.user_id=? GROUP BY h.anime_code, h.season_number ORDER BY MAX(h.watched_date) DESC""",
              (message.from_user.id,), "all")
    if not rows:
        await message.answer("🕘 Tarixingiz hozircha bo'sh.")
        return
    text = "🕘 <b>Tomosha tarixi</b>\n\n"
    for title, code, season, last_ep, _ in rows[:20]:
        text += f"• {title} — {season}-fasl, {last_ep}-qismgacha\n"
    await message.answer(text, parse_mode="HTML")


@router.message(Command("reklama"))
async def cmd_reklama(message: Message):
    await message.answer(f"📢 Kanalingizni reklama qildirmoqchi bo'lsangiz, shu adminga yozing:\n{ADMIN_USERNAME}")


# ============ ADMIN QO'SHISH/OLISH ============
# ============ ADMIN PANEL: BO'LIMLAR (navigatsiya) ============
@router.message(F.text == BTN_BACK)
async def nav_back(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer("🛠 Admin panel", reply_markup=admin_menu())


@router.message(F.text == BTN_SEC_ADMIN)
async def nav_sec_admin(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer("👑 Admin / 👤 Foydalanuvchi bo'limi", reply_markup=admin_users_menu())


@router.message(F.text == BTN_SEC_ANIME)
async def nav_sec_anime(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer("📥 Anime qo'shish / 📤 olish bo'limi", reply_markup=admin_anime_menu())


@router.message(F.text == BTN_SEC_PARAMS)
async def nav_sec_params(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer("⚙️ Parametrlar bo'limi", reply_markup=admin_params_menu())


# ============ ADMIN QO'SHISH / OLISH / RO'YXAT ============
@router.message(F.text == BTN_ADMIN_ADD)
async def btn_admin_add(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await message.answer("Yangi admin qilmoqchi bo'lgan odamning Telegram ID raqamini yuboring:")
    await state.set_state(AdminFSM.waiting_new_admin_id)


@router.message(F.text == BTN_ADMIN_DEL)
async def btn_admin_del(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    owner_id = get_owner_id()
    admins = sorted(db("SELECT user_id, username FROM admins", (), "all"), key=lambda a: a[0] != owner_id)
    rows = []
    for aid, uname in admins:
        emoji = "👑" if aid == owner_id else "🎩"
        label = await admin_label(aid, uname)
        rows.append([InlineKeyboardButton(text=f"{emoji} {label}"[:60], callback_data=f"adm:sel:{aid}")])
    text = "➖ Olib tashlamoqchi bo'lgan adminni tanlang:\n👑 — bosh admin (olib tashlab bo'lmaydi)\n🎩 — oddiy admin"
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.message(F.text == BTN_ADMIN_LIST)
async def btn_admin_list(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    owner_id = get_owner_id()
    admins = sorted(db("SELECT user_id, username FROM admins", (), "all"), key=lambda a: a[0] != owner_id)
    lines = ["📋 Adminlar ro'yxati:\n"]
    for aid, uname in admins:
        emoji = "👑" if aid == owner_id else "🎩"
        label = await admin_label(aid, uname)
        lines.append(f"{emoji} {label} — ID: {aid}")
    lines.append("\n👑 — bosh admin, 🎩 — oddiy admin")
    await message.answer("\n".join(lines))


# ============ 📒 ANIME NEWS (admin: boshlash tugmalari) ============
def news_admin_dates_kb(prefix: str, with_new: bool, style: str = "primary") -> InlineKeyboardMarkup:
    rows, row = [], []
    for did, label in news_dates():
        row.append(ibtn(f"📅 {label}"[:40], f"{prefix}:d:{did}", style=style))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    if with_new:
        rows.append([ibtn("➕ Yangi kun qo'shish", "na:new", style="success")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _ask_news_date(target: Message, state: FSMContext):
    await state.set_state(AdminFSM.waiting_news_date)
    await target.answer(
        "📅 Qaysi kun uchun? Kunni yozing.\n\nMasalan: 1 okt\n\nBekor qilish: /start"
    )


@router.message(F.text == BTN_NEWS)
async def btn_news_admin(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    if not news_dates():
        await _ask_news_date(message, state)
        return
    await message.answer(
        "📒 Anime news — qaysi kunga anime qo'shamiz?\nKunni tanlang yoki yangi kun qo'shing 👇",
        reply_markup=news_admin_dates_kb("na", with_new=True),
    )


@router.message(F.text == BTN_NEWS_DEL)
async def btn_news_del(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    if not news_dates():
        await message.answer("ℹ️ Anime news bo'limida hozircha hech narsa yo'q.")
        return
    await message.answer("🚫 News delete — qaysi kundagi animeni olib tashlaymiz?",
                         reply_markup=news_admin_dates_kb("nx", with_new=False))


# ============ 📔 VIP TAGI / ✈️ XABAR (boshlash tugmalari) ============
# ============ ✏️ BOSH SAHIFA TAGI ============
@router.message(F.text == BTN_HOME_TAG)
async def btn_home_tag(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [ibtn("✏️ Yangi matn yozish", "hm:edit", style="primary")],
        [ibtn("👁 Hozirgisini ko'rish", "hm:show", style="success")],
        [ibtn("♻️ Asl holiga qaytarish", "hm:reset", style="danger")],
    ])
    await message.answer(
        "✏️ Bosh sahifa tagi: foydalanuvchi /start bosganda chiqadigan yozuvni tahrirlash.",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("hm:"))
async def cb_home_tag(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    what = callback.data.split(":", 1)[1]
    if what == "edit":
        await state.set_state(AdminFSM.waiting_hometext)
        await callback.message.answer(
            "✏️ Yangi bosh sahifa matnini yuboring.\n"
            "Qalin, kursiv va iqtibos formatlari saqlanadi.\n\n"
            "Bekor qilish: /start"
        )
    elif what == "show":
        text, kb = main_view()
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
    elif what == "reset":
        set_setting("home_caption", "")
        await callback.message.answer("♻️ Bosh sahifa matni asl holiga qaytarildi.")
        text, kb = main_view()
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.message(StateFilter(AdminFSM.waiting_hometext))
async def process_hometext(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    if not message.text:
        await message.answer("❗️ Faqat matn yuboring.")
        return
    html_text = message.html_text
    if len(html_text) > 3000:
        await message.answer(f"❗️ Matn juda uzun ({len(html_text)} belgi). 3000 belgidan oshmasin.")
        return
    previous = get_setting("home_caption", "")
    set_setting("home_caption", html_text)
    await state.clear()
    text, kb = main_view()
    try:
        await message.answer("✅ Bosh sahifa tagi yangilandi. Foydalanuvchi shunday ko'radi:")
        await message.answer(text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest as e:
        logging.error(e)
        set_setting("home_caption", previous)
        await message.answer("⚠️ Matnni ko'rsatib bo'lmadi, eski matn saqlab qolindi. "
                             "Formatni tekshirib, qayta yuboring.")


# ============ ✏️ TAGLARNI TAHRIRLASH (umumiy bo'lim) ============
@router.message(F.text == BTN_TAGS)
async def btn_tags(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [ibtn("🎬 Anime tagi", "tg:anime", style="success"), ibtn("💎 VIP tagi", "tg:vip", style="primary")],
        [ibtn("📀 Qism tagi", "tg:ep", style="success"), ibtn("🏠 Bosh sahifa tagi", "tg:home", style="primary")],
        [ibtn("❤️ Obunalarim tagi", "tg:sub", style="danger"), ibtn("⭐️ Yoqqanlar tagi", "tg:fav", style="success")],
        [ibtn("🕝 Keyinroq tagi", "tg:later", style="primary"), ibtn("👤 Profil tagi", "tg:profile", style="primary")],
    ])
    await message.answer("✏️ Taglarni tahrirlash\nQaysi bo'limning tagini o'zgartiramiz?", reply_markup=kb)


@router.callback_query(F.data.startswith("tg:"))
async def cb_tags(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    key = callback.data.split(":", 1)[1]
    msg = callback.message
    if key == "anime":
        await state.set_state(AdminFSM.waiting_edittag_find)
        await msg.answer("✏️ Tagini tahrirlamoqchi bo'lgan animening nomini yoki kodini yozing:")
    elif key == "vip":
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [ibtn("📝 VIP bo'lim matni", "vt:cap", style="primary")],
            [ibtn("💳 Karta ma'lumotlari", "vt:card", style="success")],
            [ibtn("♻️ Asl holiga qaytarish", "vt:reset", style="danger")],
        ])
        await msg.answer("💎 VIP tagi: qaysi matnni tahrirlaymiz?", reply_markup=kb)
    elif key == "home":
        await state.set_state(AdminFSM.waiting_hometext)
        await msg.answer("✏️ Yangi bosh sahifa matnini yuboring.\n"
                         "Qalin, kursiv va iqtibos formatlari saqlanadi.\n\nBekor qilish: /start")
    elif key in TAG_KEYS:
        await state.set_state(AdminFSM.waiting_tag)
        await state.update_data(tag_key=key)
        await msg.answer(f"{TAG_LABELS[key]} — hozirgisi:")
        await msg.answer(tag_preview_html(key), parse_mode="HTML")
        if key == "ep":
            hint = ("✏️ Yangi qism tagini yuboring. {nomi} — anime nomi, {fasl} — fasl, {qism} — qism raqami "
                    "o'rniga avtomatik qo'yiladi.\nBekor qilish: /start")
        else:
            hint = "✏️ Yangi matnni yuboring. Qalin, kursiv va iqtibos formatlari saqlanadi.\nBekor qilish: /start"
        kb = InlineKeyboardMarkup(inline_keyboard=[[ibtn("♻️ Asl holiga qaytarish", f"tgr:{key}", style="danger")]])
        await msg.answer(hint, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("tgr:"))
async def cb_tag_reset(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    key = callback.data.split(":", 1)[1]
    if key not in TAG_KEYS:
        await callback.answer()
        return
    set_setting(f"tag:{key}", "")
    await state.clear()
    await callback.message.answer(f"♻️ {TAG_LABELS[key]} asl holiga qaytarildi.")
    await callback.message.answer(tag_preview_html(key), parse_mode="HTML")
    await callback.answer()


@router.message(StateFilter(AdminFSM.waiting_tag))
async def process_tag(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    key = (await state.get_data()).get("tag_key")
    if key not in TAG_KEYS:
        await state.clear()
        await message.answer("⚠️ Qaytadan boshlang: ✏️ Taglarni tahrirlash")
        return
    if not message.text:
        await message.answer("❗️ Faqat matn yuboring.")
        return
    value = message.text if key == "ep" else message.html_text
    limit = 1000 if key == "ep" else 3000
    if len(value) > limit:
        await message.answer(f"❗️ Matn juda uzun ({len(value)} belgi). {limit} belgidan oshmasin.")
        return
    previous = get_setting(f"tag:{key}", "")
    set_setting(f"tag:{key}", value)
    await state.clear()
    try:
        await message.answer(f"✅ {TAG_LABELS[key]} yangilandi. Ko'rinishi:")
        await message.answer(tag_preview_html(key, value), parse_mode="HTML")
    except TelegramBadRequest as e:
        logging.error(e)
        set_setting(f"tag:{key}", previous)
        await message.answer("⚠️ Matnni ko'rsatib bo'lmadi, eski matn saqlab qolindi. "
                             "Formatni tekshirib, qayta yuboring.")


@router.message(F.text == BTN_VIP_TAG)
async def btn_vip_tag(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [ibtn("📝 VIP bo'lim matni", "vt:cap", style="primary")],
        [ibtn("💳 Karta ma'lumotlari", "vt:card", style="success")],
        [ibtn("♻️ Asl holiga qaytarish", "vt:reset", style="danger")],
    ])
    await message.answer(
        "📔 VIP tagi — qaysi matnni tahrirlaymiz?\n\n"
        "📝 VIP bo'lim matni — foydalanuvchi 💎 VIP olish ni bosganda chiqadigan yozuv.\n"
        "💳 Karta ma'lumotlari — tarif tanlanganda chiqadigan karta raqami, karta egasi, murojaat va ogohlantirish.",
        reply_markup=kb,
    )


@router.message(F.text == BTN_BROADCAST)
async def btn_broadcast(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.set_state(AdminFSM.waiting_broadcast)
    await message.answer(
        "✈️ Botdan foydalanuvchilarga yuboriladigan xabarni yuboring.\n"
        "Matn, rasm, video, fayl yoki ovoz bo'lishi mumkin.\n\n"
        "Bekor qilish: /start"
    )


@router.callback_query(F.data == "adm:add")
async def cb_admin_add(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await callback.message.answer("Yangi admin qilmoqchi bo'lgan odamning Telegram ID raqamini yuboring:")
    await state.set_state(AdminFSM.waiting_new_admin_id)
    await callback.answer()


@router.message(StateFilter(AdminFSM.waiting_new_admin_id))
async def process_new_admin(message: Message, state: FSMContext):
    await state.clear()
    try:
        new_id = int(message.text.strip())
    except ValueError:
        await message.answer("❗️ Faqat raqam (ID) yuboring.")
        return
    if new_id == get_owner_id():
        await message.answer("👑 Bu — bosh admin, u allaqachon admin.")
        return
    display_name = str(new_id)
    try:
        chat = await bot.get_chat(new_id)
        display_name = ("@" + chat.username) if chat.username else (chat.full_name or str(new_id))
    except Exception:
        pass
    db("INSERT OR REPLACE INTO admins (user_id, username, added_date) VALUES (?,?,?)",
       (new_id, display_name, datetime.now().isoformat()))
    await message.answer(f"✅ {display_name} endi admin!")
    try:
        await bot.send_message(new_id, "🎉 Sizga admin huquqi berildi!", reply_markup=admin_menu())
    except Exception:
        pass


async def admin_label(aid: int, stored) -> str:
    stored = (stored or "").strip()
    if stored and not stored.isdigit():
        return stored
    row = db("SELECT username FROM users WHERE user_id=?", (aid,), "one")
    return await user_label(aid, row[0] if row and row[0] else "")


@router.callback_query(F.data == "adm:list")
async def cb_admin_list(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    owner_id = get_owner_id()
    admins = sorted(db("SELECT user_id, username FROM admins", (), "all"), key=lambda a: a[0] != owner_id)
    rows = []
    for aid, uname in admins:
        emoji = "👑" if aid == owner_id else "🎩"
        label = await admin_label(aid, uname)
        rows.append([InlineKeyboardButton(text=f"{emoji} {label}"[:60], callback_data=f"adm:sel:{aid}")])
    text = "📋 Hozirgi adminlar:\n👑 — bosh admin (olib tashlab bo'lmaydi)\n🎩 — oddiy admin"
    if owner_id is None:
        text += f"\n\nℹ️ Bosh admin @{OWNER_USERNAME} botga yozgan zahoti avtomatik 👑 bo'ladi va himoyalanadi."
    await callback.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data.startswith("adm:sel:"))
async def cb_admin_select(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    target_id = int(callback.data.split(":")[2])
    row = db("SELECT username FROM admins WHERE user_id=?", (target_id,), "one")
    label = await admin_label(target_id, row[0] if row else "")
    if target_id == get_owner_id():
        await callback.message.answer(f"👑 {label}\nBu — bosh admin. Uning admin huquqini hech kim olib tashlay olmaydi.")
        await callback.answer()
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Admin olib tashlash", callback_data=f"adm:rm:{target_id}")],
        [InlineKeyboardButton(text="✅ Yo'q, admin qolsin", callback_data="adm:keep")],
    ])
    await callback.message.answer(f"🎩 {label}\nNima qilamiz?", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("adm:rm:"))
async def cb_admin_remove(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    target_id = int(callback.data.split(":")[2])
    if target_id == get_owner_id():
        await callback.answer("👑 Bosh adminni olib bo'lmaydi.", show_alert=True)
        return
    row = db("SELECT username FROM admins WHERE user_id=?", (target_id,), "one")
    label = await admin_label(target_id, row[0] if row else "")
    db("DELETE FROM admins WHERE user_id=?", (target_id,))
    await callback.message.answer(f"🗑 {label} admin huquqidan olib tashlandi.")
    await callback.answer()


@router.callback_query(F.data == "adm:keep")
async def cb_admin_keep(callback: CallbackQuery):
    await callback.message.answer("✅ O'zgarishsiz qoldi.")
    await callback.answer()


# ============ FOYDALANUVCHILAR ============
# ============ 👥 FOYDALANUVCHILAR (admin) ============
USERS_PAGE_SIZE = 10


def users_stats_view(page: int):
    total = db("SELECT COUNT(*) FROM users", (), "one")[0]
    cutoff = (datetime.now() - timedelta(minutes=ONLINE_WINDOW_MIN)).isoformat()
    online = db("SELECT COUNT(*) FROM users WHERE last_seen >= ?", (cutoff,), "one")[0]
    admins_total = db("SELECT COUNT(*) FROM admins", (), "one")[0]
    vip_total = sum(1 for (uid,) in db("SELECT user_id FROM users WHERE is_vip=1", (), "all") if is_vip(uid))

    users = db("SELECT user_id, username FROM users ORDER BY joined_date DESC", (), "all")
    pages = max(1, (len(users) + USERS_PAGE_SIZE - 1) // USERS_PAGE_SIZE)
    page = max(1, min(page, pages))
    chunk = users[(page - 1) * USERS_PAGE_SIZE: page * USERS_PAGE_SIZE]

    text = quote(
        "👥 <b>Foydalanuvchilar statistikasi</b>\n\n"
        f"👤 Botda jami: {total} kishi\n"
        f"🟢 Hozir online: {online} kishi\n"
        f"👑 Adminlar: {admins_total} ta\n"
        f"💎 VIP foydalanuvchilar: {vip_total} ta"
    ) + "\n\n👇 Foydalanuvchini tanlang, uning profili chiqadi"
    rows, row = [], []
    for uid, uname in chunk:
        label = f"@{uname}" if uname else f"ID {uid}"
        row.append(ibtn(f"👤 {label}"[:40], f"uv:{uid}:{page}", style="primary"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    back_cb = f"us:{page - 1}" if page > 1 else "first"
    fwd_cb = f"us:{page + 1}" if page < pages else "end"
    rows.append(nav_row(back_cb, page, pages, fwd_cb))
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def user_profile_view(uid: int, page: int):
    row = db("SELECT username, joined_date, last_seen FROM users WHERE user_id=?", (uid,), "one")
    if not row:
        return None
    uname, joined, last_seen = row
    full_name = ""
    try:
        chat = await bot.get_chat(uid)
        full_name = " ".join(x for x in (chat.first_name, chat.last_name) if x)
    except Exception:
        pass
    vip_line = "❌ Yo'q"
    if is_vip(uid):
        r = db("SELECT vip_until, vip_plan FROM users WHERE user_id=?", (uid,), "one")
        vip_line = f"✅ {PLAN_LABELS.get(r[1] or ('forever' if r[0] == 'forever' else ''), 'VIP')} — {fmt_vip_until(r[0])}"
    cutoff = (datetime.now() - timedelta(minutes=ONLINE_WINDOW_MIN)).isoformat()
    online = "🟢 Online" if (last_seen or "") >= cutoff else f"⚪️ {(last_seen or '—')[:16].replace('T', ' ')}"
    watched = db("SELECT COUNT(*) FROM history WHERE user_id=?", (uid,), "one")[0]
    lines = [
        "👤 <b>Foydalanuvchi profili</b>\n",
        f"🆔 ID: <code>{uid}</code>",
        f"👤 Ism: <a href=\"tg://user?id={uid}\">{esc(full_name or 'Nomalum')}</a>",
        f"🔗 Username: {('@' + esc(uname)) if uname else '—'}",
        f"👑 Admin: {'ha' if is_admin(uid) else 'yo' + chr(39) + 'q'}",
        f"💎 VIP: {esc(vip_line)}",
        f"📅 Qo'shilgan: {(joined or '—')[:10]}",
        f"🕒 Oxirgi faollik: {online}",
        f"📀 Ko'rgan qismlari: {watched} ta",
        f"❤️ Obunalari: {count_list(uid, 'sub')} ta",
        f"⭐️ Yoqqanlari: {count_list(uid, 'fav')} ta",
        f"🕝 Keyinroqda: {count_list(uid, 'later')} ta",
    ]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [ibtn("💎 VIP berish", f"uvg:{uid}:{page}", style="success"),
         ibtn("🚫 VIP olish", f"vr:{uid}", style="danger")],
        [ibtn("🔙 Orqaga", f"us:{page}", style="danger")],
    ])
    return quote("\n".join(lines)), kb


@router.message(F.text == BTN_USERS_STAT)
async def btn_users(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    text, kb = users_stats_view(1)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("us:"))
async def cb_users_page(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    try:
        page = int(callback.data.split(":")[1])
    except ValueError:
        page = 1
    text, kb = users_stats_view(page)
    await edit_view(callback, text, kb, prefer_text=True)
    await callback.answer()


@router.callback_query(F.data.startswith("uv:"))
async def cb_user_profile(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    try:
        _, uid, page = callback.data.split(":")
        view = await user_profile_view(int(uid), int(page))
    except ValueError:
        await callback.answer()
        return
    if not view:
        await callback.answer("⚠️ Foydalanuvchi topilmadi.", show_alert=True)
        return
    text, kb = view
    await edit_view(callback, text, kb, prefer_text=True)
    await callback.answer()


@router.callback_query(F.data.startswith("uvg:"))
async def cb_user_vip_plans(callback: CallbackQuery):
    """Foydalanuvchi profilidan VIP berish: tarif tugmalari."""
    if not is_admin(callback.from_user.id):
        return
    try:
        _, uid, page = callback.data.split(":")
        uid, page = int(uid), int(page)
    except ValueError:
        await callback.answer()
        return
    rows = [[ibtn(PLAN_LABELS[k].replace("📅 ", ""), f"vg:{uid}:{k}", style="success")] for k in PLAN_ORDER]
    rows.append([ibtn("🔙 Orqaga", f"uv:{uid}:{page}", style="danger")])
    await edit_view(callback, quote("💎 Qancha muddatga VIP beramiz?"), InlineKeyboardMarkup(inline_keyboard=rows),
                    prefer_text=True)
    await callback.answer()


# ============ ANIME QO'SHISH ============
@router.message(F.text == "📥 Anime qo'shish")
async def btn_add_anime(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.set_state(AdminFSM.waiting_anime)
    await message.answer(
        "🖼 Anime uchun rasm (poster) yuboring.\n\n"
        "Kod: 876\n"
        "Nomi: Tokyo qasoskorlari 4-fasl\n"
        "Fasllar: 4\n"
        "Qismi: ?\n"
        "Janr: #jangari\n"
        "Kanal: @an1zenuz\n"
        "Premium: yo'q",
    )


def _parse_anime_caption(caption: str):
    """Kod, Premium, Yangilash — ko'rinmaydi. Nomi — qidiruv nomi uchun olinadi, lekin ko'rinadi.
    Qolgan hamma narsa captionda aynan yozilganidek qoladi."""
    control, kept = {}, []
    for line in (caption or "").split("\n"):
        if ":" in line:
            key, val = line.split(":", 1)
            k = key.strip().lower()
            if k in ("kod", "premium", "yangilash"):
                control[k] = val.strip()
                continue
            if k == "nomi":
                control["nomi"] = val.strip()
        kept.append(line.rstrip())
    return control, "\n".join(kept).strip()


def _parse_premium(val):
    v = re.sub(r"[^a-z]", "", (val or "").lower())  # "yo'q" -> "yoq"
    if v in ("ha", "premium", "vip", "bor"):
        return 1
    if v in ("yoq", "no", "yoqq"):
        return 0
    return None


def _next_anime_code() -> str:
    rows = db("SELECT code FROM animes", (), "all")
    nums = [int(r[0]) for r in rows if str(r[0]).isdigit()]
    return str(max(nums) + 1 if nums else 1)


@router.message(F.photo, StateFilter(AdminFSM.waiting_anime))
@router.message(F.photo, F.caption.lower().contains("kod:"))
async def process_addanime(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    if await state.get_state() == AdminFSM.waiting_anime.state:
        await state.clear()
    control, free_text = _parse_anime_caption(message.caption or "")

    code = control.get("kod", "")
    auto_code = False
    if not code:
        code = _next_anime_code()
        auto_code = True
    elif " " in code or ":" in code:
        await message.answer("❌ Kod bo'sh joysiz bitta so'z bo'lishi kerak (masalan: Kod: 25).")
        return

    if control.get("premium"):
        is_premium = _parse_premium(control["premium"])
        if is_premium is None:
            await message.answer("❌ Premium qiymati tushunarsiz. Yozing: Premium: ha  yoki  Premium: yo'q "
                                 "(yoki bu qatorni olib tashlang).")
            return
    else:
        is_premium = 0

    first_line = next((ln.strip() for ln in free_text.split("\n") if ln.strip()), "")
    title = (control.get("nomi") or first_line or code)[:100]

    existing = db("SELECT title FROM animes WHERE code=?", (code,), "one")
    force_update = control.get("yangilash", "").lower() in ("ha", "yes", "1")
    if existing and existing[0] != title and not force_update:
        await message.answer(
            f"⚠️ Bu kod (\"{code}\") allaqachon \"{existing[0]}\" animesi uchun band. "
            f"Boshqa kod tanlang.\n\nAgar shu animening rasmi/captionini yangilamoqchi bo'lsangiz, "
            f"captionga \"Yangilash: ha\" qatorini qo'shing."
        )
        return

    poster_file_id = message.photo[-1].file_id
    db("""INSERT OR REPLACE INTO animes
          (code, title, total_seasons, total_episodes_declared, genre, channel_name,
           quality, rating, views, downloads, is_premium, poster_file_id, added_date, custom_caption)
          VALUES (?,?,?,?,?,?,
                  COALESCE((SELECT quality FROM animes WHERE code=?),'-'),
                  COALESCE((SELECT rating FROM animes WHERE code=?),'-'),
                  COALESCE((SELECT views FROM animes WHERE code=?),0),
                  COALESCE((SELECT downloads FROM animes WHERE code=?),0),
                  ?,?,?,?)""",
       (code, title, 0, 0, "-", "",
        code, code, code, code, is_premium, poster_file_id, datetime.now().isoformat(),
        free_text or None))
    sync_total_seasons(code)
    asyncio.create_task(ensure_poster_url(code, poster_file_id))

    notes = ""
    if auto_code:
        notes += f"\n🔢 Kod yozilmagani uchun avtomatik berildi: {code}"
    if not free_text:
        notes += "\nℹ️ Caption yozilmagani uchun anime kodi nom sifatida ishlatildi."
    await message.answer(
        f"✅ Anime {'yangilandi' if existing else 'qo' + chr(39) + 'shildi'}!\n"
        f"Kod: {code}\n"
        f"🔎 Qidiruvdagi nomi: {title}\n"
        f"💎 Premium: {'ha' if is_premium else 'yoq'}"
        f"{notes}\n\n"
        f"Endi qismlarini yuklang:\n/addep {code} 1 1\n(video caption'i yoki videoga reply qilib)\n\n"
        f"Kanalga e'lon qilish uchun \"📣 Reklama\" tugmasini bosib, kodni ({code}) yuboring."
    )


# ============ ANIME OLISH (o'chirish) ============
@router.message(F.text == "📤 Anime olish")
async def btn_delete_anime(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await message.answer("🗑 O'chirmoqchi bo'lgan animening kodini yuboring:")
    await state.set_state(AdminFSM.waiting_delete_anime)


@router.message(StateFilter(AdminFSM.waiting_delete_anime))
async def process_delete_anime(message: Message, state: FSMContext):
    await state.clear()
    code = message.text.strip()
    row = db("SELECT title FROM animes WHERE code=?", (code,), "one")
    if not row:
        await message.answer("❌ Bunday kodli anime topilmadi.")
        return
    db("DELETE FROM animes WHERE code=?", (code,))
    db("DELETE FROM episodes WHERE anime_code=?", (code,))
    db("DELETE FROM history WHERE anime_code=?", (code,))
    db("DELETE FROM seasons WHERE anime_code=?", (code,))
    db("DELETE FROM user_lists WHERE anime_code=?", (code,))
    await message.answer(f"🗑 \"{row[0]}\" (kod: {code}) butunlay o'chirildi.")


# ============ QISM QO'SHISH ============
@router.message(F.text == "📥 Qism qo'shish")
async def btn_add_episode(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "➕ Qism qo'shish:\n\n"
        "Videoni botga yuboring (yoki forward qiling). Video caption'iga (forward bo'lsa "
        "videoga reply qilib) shunday yozing:\n\n"
        "/addep KOD FASL QISM\n"
        "O'zingiz xohlagan yozuv (nom, fasl, qism...)\n\n"
        "Masalan:\n"
        "/addep 25 2 5\n"
        "Naruto | 2-fasl | 5-qism\n\n"
        "Birinchi qatordan pastdagi hamma yozuv shu videoning captioni bo'ladi. "
        "Har bir video uchun alohida yoziladi. Agar pastiga hech narsa yozmasangiz, "
        "caption avtomatik (nom, fasl, qism) qo'yiladi."
    )


def _save_episode(code: str, season: int, num: int, file_id: str, caption):
    """Qismni saqlaydi. (xato_matni yoki None, yangi_fasl_yaratildimi) qaytaradi."""
    if season < 1 or num < 1:
        return "❗️ Fasl va qism 1 dan katta bo'lishi kerak.", False
    if not db("SELECT 1 FROM animes WHERE code=?", (code,), "one"):
        return "❌ Bunday kodli anime topilmadi, avval anime qo'shing.", False
    if caption and len(caption) > 1024:
        return f"❗️ Caption juda uzun ({len(caption)} belgi). 1024 belgidan oshmasin.", False
    is_new_season = season not in get_seasons(code)
    db("INSERT OR REPLACE INTO episodes (anime_code, season_number, episode_number, file_id, episode_title) VALUES (?,?,?,?,?)",
       (code, season, num, file_id, caption or None))
    db("INSERT OR IGNORE INTO seasons (anime_code, season_number) VALUES (?,?)", (code, season))
    sync_total_seasons(code)
    return None, is_new_season


def _split_addep(first_line_tokens, rest_text):
    """['25','2','5','Qo'shimcha'] + pastki qatorlar -> (code, season, num, caption)"""
    if len(first_line_tokens) < 3:
        return None
    code = first_line_tokens[0]
    try:
        season, num = int(first_line_tokens[1]), int(first_line_tokens[2])
    except ValueError:
        return None
    parts = []
    if len(first_line_tokens) > 3:
        parts.append(" ".join(first_line_tokens[3:]))
    if rest_text and rest_text.strip():
        parts.append(rest_text.strip())
    caption = "\n".join(parts) if parts else None
    return code, season, num, caption


async def _finish_addep(message: Message, parsed, file_id: str):
    code, season, num, caption = parsed
    before = count_episodes(code)
    err, is_new_season = _save_episode(code, season, num, file_id, caption)
    if err:
        await message.answer(err)
        return
    after = count_episodes(code)
    text = f"✅ {code}-anime, {season}-fasl, {num}-qism qo'shildi!\n📀 Qismi: {before} → {after}"
    if is_new_season:
        text += f"\n🎞 {season}-fasl avtomatik qo'shildi."
    if caption:
        text += f"\n\n📝 Caption:\n{caption}"
    await message.answer(text)


@router.message(F.video, F.caption, F.caption.startswith("/addep"))
async def process_addep_direct(message: Message):
    if not is_admin(message.from_user.id):
        return
    lines = message.caption.split("\n", 1)
    tokens = lines[0].split()[1:]  # "/addep" ni tashlab yuboramiz
    parsed = _split_addep(tokens, lines[1] if len(lines) > 1 else "")
    if not parsed:
        await message.answer("❗️ Format: /addep KOD FASL QISM  (masalan: /addep 25 1 1)\nPastiga caption yozing.")
        return
    await _finish_addep(message, parsed, message.video.file_id)


@router.message(Command("addep"))
async def cmd_addep_reply(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    if message.reply_to_message and message.reply_to_message.video and command.args:
        lines = command.args.split("\n", 1)
        parsed = _split_addep(lines[0].split(), lines[1] if len(lines) > 1 else "")
        if not parsed:
            await message.answer("❗️ Format: /addep KOD FASL QISM\nPastiga caption yozing.")
            return
        await _finish_addep(message, parsed, message.reply_to_message.video.file_id)
        return
    await message.answer(
        "❗️ Videoni caption bilan yuboring (/addep KOD FASL QISM), "
        "yoki videoga javob (reply) qilib shu buyruqni yozing."
    )


# ============ QISM OLISH ============
@router.message(F.text == "📤 Qism olish")
async def btn_delete_episode(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await message.answer("🗑 O'chirmoqchi bo'lgan qismni <code>KOD FASL QISM</code> shaklida yuboring (masalan: 25 1 3):",
                          parse_mode="HTML")
    await state.set_state(AdminFSM.waiting_delete_episode)


@router.message(StateFilter(AdminFSM.waiting_delete_episode))
async def process_delete_episode(message: Message, state: FSMContext):
    await state.clear()
    parts = message.text.strip().split()
    if len(parts) != 3:
        await message.answer("❗️ Format: KOD FASL QISM")
        return
    code, season, num = parts
    try:
        season, num = int(season), int(num)
    except ValueError:
        await message.answer("❗️ Fasl va qism raqam bo'lishi kerak.")
        return
    db("DELETE FROM episodes WHERE anime_code=? AND season_number=? AND episode_number=?", (code, season, num))
    await message.answer(f"🗑 {code}-anime, {season}-fasl, {num}-qism o'chirildi.")


# ============ KODNI O'ZGARTIRISH ============
@router.message(F.text == "🔢 Kodni o'zgartirish")
async def btn_change_code(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await message.answer("✏️ O'zgartirmoqchi bo'lgan animening HOZIRGI kodini yuboring:")
    await state.set_state(AdminFSM.waiting_changecode_old)


@router.message(StateFilter(AdminFSM.waiting_changecode_old))
async def process_changecode_old(message: Message, state: FSMContext):
    old_code = message.text.strip()
    row = db("SELECT title FROM animes WHERE code=?", (old_code,), "one")
    if not row:
        await message.answer("❌ Bunday kodli anime topilmadi.")
        await state.clear()
        return
    await state.update_data(old_code=old_code, title=row[0])
    await message.answer(f"\"{row[0]}\" uchun YANGI kodni yuboring:")
    await state.set_state(AdminFSM.waiting_changecode_new)


@router.message(StateFilter(AdminFSM.waiting_changecode_new))
async def process_changecode_new(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    old_code = data["old_code"]
    title = data["title"]
    new_code = message.text.strip()
    if db("SELECT 1 FROM animes WHERE code=?", (new_code,), "one"):
        await message.answer("❌ Bu kod allaqachon band, boshqa kod tanlang.")
        return
    db("UPDATE animes SET code=? WHERE code=?", (new_code, old_code))
    db("UPDATE episodes SET anime_code=? WHERE anime_code=?", (new_code, old_code))
    db("UPDATE history SET anime_code=? WHERE anime_code=?", (new_code, old_code))
    db("UPDATE seasons SET anime_code=? WHERE anime_code=?", (new_code, old_code))
    db("UPDATE user_lists SET anime_code=? WHERE anime_code=?", (new_code, old_code))
    await message.answer(f"✅ \"{title}\" kodi {old_code} dan {new_code} ga o'zgartirildi.")


# ============ KODLAR RO'YXATI ============
@router.message(F.text == "🔢 Kodlar ro'yxati")
async def btn_codes_list(message: Message):
    if not is_admin(message.from_user.id):
        return
    animes = db("SELECT code, title FROM animes ORDER BY title", (), "all")
    if not animes:
        await message.answer("📋 Hozircha animelar yo'q.")
        return
    text = "📋 <b>Kodlar ro'yxati</b>\n\n" + "\n".join([f"• <code>{c}</code> — {t}" for c, t in animes])
    if len(text) > 4000:
        text = text[:4000] + "\n\n... (ro'yxat juda uzun)"
    await message.answer(text, parse_mode="HTML")


# ============ QISM POST QILISH ============
@router.message(F.text == "👀 Qism post qilish")
async def btn_postep(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "📢 Kanalga post qilmoqchi bo'lgan (allaqachon botga qo'shilgan) qismni "
        "KOD FASL QISM shaklida yuboring.\n"
        "Qaysi qism raqamini yozsangiz, postdagi tugmada ham aynan shu qism raqami chiqadi."
    )
    await state.set_state(AdminFSM.waiting_postep)


def _build_ep_post(code: str, season: int, num: int):
    anime = db("SELECT title, poster_file_id FROM animes WHERE code=?", (code,), "one")
    ep = db("SELECT episode_title FROM episodes WHERE anime_code=? AND season_number=? AND episode_number=?",
            (code, season, num), "one")
    if not anime or not ep:
        return None
    title, poster_file_id = anime
    caption = fit_quotes(ep[0] if ep[0] else ep_default_caption(title, season, num), 1024)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"⭐️ {num} - qismni ko'rish ⭐️",
                             url=f"https://t.me/{BOT_USERNAME}?start={code}_{season}_{num}")
    ]])
    return poster_file_id, caption, kb


def _build_anime_post(code: str):
    row = db("SELECT title, poster_file_id, custom_caption FROM animes WHERE code=?", (code,), "one")
    if not row:
        return None
    title, poster_file_id, custom_caption = row
    caption = fit_quotes(render_qismi(custom_caption, count_episodes(code)) if custom_caption else f"🎬 {title}", 1024)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💈 Anime Ko'rish 💈", url=f"https://t.me/{BOT_USERNAME}?start={code}")
    ]])
    return title, poster_file_id, caption, kb


@router.message(StateFilter(AdminFSM.waiting_postep))
async def process_postep(message: Message, state: FSMContext):
    await state.clear()
    parts = (message.text or "").strip().split()
    if len(parts) != 3:
        await message.answer("❗️ Format: KOD FASL QISM")
        return
    code, season, num = parts
    try:
        season, num = int(season), int(num)
    except ValueError:
        await message.answer("❗️ Fasl va qism raqam bo'lishi kerak.")
        return
    if not db("SELECT 1 FROM animes WHERE code=?", (code,), "one"):
        await message.answer("❌ Bunday kodli anime/drama topilmadi.")
        return
    if not _build_ep_post(code, season, num):
        await message.answer("❌ Bu qism hali botga qo'shilmagan. Avval \"📥 Qism qo'shish\" orqali qo'shing.")
        return
    if not get_channels():
        await message.answer("⚠️ Hali reklama kanali qo'shilmagan. Avval \"📢 Kanal reklama\" orqali kanal qo'shing.")
        return
    if len(f"pe:9999:{code}:{season}:{num}".encode()) > 64:
        await message.answer("❗️ Kod juda uzun. Qisqaroq kod tanlang (\"🔢 Kodni o'zgartirish\").")
        return
    await message.answer(
        f"📢 {num}-qismni qaysi kanalga post qilay?",
        reply_markup=channel_pick_kb(lambda cid: f"pe:{cid}:{code}:{season}:{num}"),
    )


@router.callback_query(F.data.startswith("pe:"))
async def cb_post_episode(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    try:
        _, cid, code, season, num = callback.data.split(":", 4)
        cid, season, num = int(cid), int(season), int(num)
    except ValueError:
        await callback.answer("⚠️ Bajarilmadi.", show_alert=True)
        return
    channel = db("SELECT chat_id, title FROM post_channels WHERE id=?", (cid,), "one")
    post = _build_ep_post(code, season, num)
    if not channel or not post:
        await callback.answer("⚠️ Kanal yoki qism topilmadi.", show_alert=True)
        return
    try:
        await post_to_channel(channel[0], *post)
        await callback.message.answer(f"✅ {num}-qism \"{channel[1]}\" kanaliga post qilindi!")
    except Exception as e:
        logging.error(e)
        await callback.message.answer(channel_error_text(e))
    await callback.answer()


# ============ 📣 REKLAMA (anime yoki drama, tanlangan kanalga) ============
@router.message(F.text == "📣 Reklama")
async def btn_postanime(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await message.answer("📣 Reklama qilmoqchi bo'lgan anime yoki dramaning kodini yuboring:")
    await state.set_state(AdminFSM.waiting_postanime)


@router.message(StateFilter(AdminFSM.waiting_postanime))
async def process_postanime(message: Message, state: FSMContext):
    await state.clear()
    code = (message.text or "").strip()
    post = _build_anime_post(code)
    if not post:
        await message.answer("❌ Bunday kodli anime/drama topilmadi.")
        return
    if not get_channels():
        await message.answer("⚠️ Hali reklama kanali qo'shilmagan. Avval \"📢 Kanal reklama\" orqali kanal qo'shing.")
        return
    if len(f"pa:9999:{code}".encode()) > 64:
        await message.answer("❗️ Kod juda uzun. Qisqaroq kod tanlang (\"🔢 Kodni o'zgartirish\").")
        return
    await message.answer(
        f"📣 \"{post[0]}\" qaysi kanalga reklama qilinsin?",
        reply_markup=channel_pick_kb(lambda cid: f"pa:{cid}:{code}"),
    )


@router.callback_query(F.data.startswith("pa:"))
async def cb_post_anime(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    try:
        _, cid, code = callback.data.split(":", 2)
        cid = int(cid)
    except ValueError:
        await callback.answer("⚠️ Bajarilmadi.", show_alert=True)
        return
    channel = db("SELECT chat_id, title FROM post_channels WHERE id=?", (cid,), "one")
    post = _build_anime_post(code)
    if not channel or not post:
        await callback.answer("⚠️ Kanal yoki anime topilmadi.", show_alert=True)
        return
    title, poster_file_id, caption, kb = post
    try:
        await post_to_channel(channel[0], poster_file_id, caption, kb)
        await callback.message.answer(f"✅ \"{title}\" \"{channel[1]}\" kanaliga reklama qilindi!")
    except Exception as e:
        logging.error(e)
        await callback.message.answer(channel_error_text(e))
    await callback.answer()


# ============ MAJBURIY OBUNA BOSHQARUVI ============
@router.message(F.text == "🔒 Majburiy obuna")
async def btn_force_sub(message: Message):
    if not is_admin(message.from_user.id):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Kanal qo'shish", callback_data="fs:add")],
        [InlineKeyboardButton(text="📋 Ro'yxat / olib tashlash", callback_data="fs:list")],
    ])
    await message.answer("🔒 Majburiy obuna kanallari:", reply_markup=kb)


@router.callback_query(F.data == "fs:add")
async def cb_fs_add(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await callback.message.answer(
        "➕ Kanalni qo'shishning 2 usuli bor:\n"
        "1) Botni ADMIN qilib qo'shgan kanalingizdan istalgan xabarni shu botga FORWARD qiling\n"
        "2) Yoki kanal ID raqamini (masalan -1001234567890) to'g'ridan-to'g'ri yozing "
        "(bot o'sha kanalda admin bo'lishi shart)"
    )
    await state.set_state(AdminFSM.waiting_forcesub_forward)
    await callback.answer()


@router.message(StateFilter(AdminFSM.waiting_forcesub_forward))
async def process_fs_forward(message: Message, state: FSMContext):
    await state.clear()

    if not message.forward_from_chat and message.text:
        try:
            ch_id = int(message.text.strip())
        except ValueError:
            await message.answer("❗️ Bu — forward qilingan xabar ham, to'g'ri kanal ID ham emas.")
            return
        try:
            chat = await bot.get_chat(ch_id)
        except TelegramBadRequest:
            await message.answer("❗️ Bu ID bo'yicha kanal topilmadi, yoki bot u yerda emas.")
            return
    elif message.forward_from_chat:
        chat = message.forward_from_chat
        ch_id = chat.id
    else:
        await message.answer("❗️ Kanaldan xabar forward qiling, yoki kanal ID raqamini yuboring.")
        return

    if chat.username:
        join_url = f"https://t.me/{chat.username}"
        display_name = f"@{chat.username}"
    else:
        try:
            join_url = await bot.export_chat_invite_link(ch_id)
        except TelegramBadRequest:
            await message.answer("⚠️ Kanal havolasini yarata olmadim — bot shu kanalda admin (taklif havolasi yaratish huquqi bilan) ekanini tekshiring.")
            return
        display_name = chat.title or str(ch_id)
    db("INSERT OR REPLACE INTO force_sub_channels (channel_id, display_name, join_url) VALUES (?,?,?)",
       (ch_id, display_name, join_url))
    await message.answer(f"✅ {display_name} majburiy obuna ro'yxatiga qo'shildi.")


@router.callback_query(F.data == "fs:list")
async def cb_fs_list(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    channels = db("SELECT channel_id, display_name FROM force_sub_channels", (), "all")
    if not channels:
        await callback.message.answer("📋 Majburiy obuna kanallari hozircha yo'q.")
        await callback.answer()
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🗑 {name}", callback_data=f"fs:rm:{cid}")] for cid, name in channels
    ])
    await callback.message.answer("📋 Kanal — bosilsa ro'yxatdan olib tashlanadi:", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("fs:rm:"))
async def cb_fs_remove(callback: CallbackQuery):
    ch_id = int(callback.data.split(":")[2])
    db("DELETE FROM force_sub_channels WHERE channel_id=?", (ch_id,))
    await callback.message.answer("🗑 Kanal majburiy obuna ro'yxatidan olib tashlandi.")
    await callback.answer()


# ============ 📢 KANAL REKLAMA (reklama kanallari ro'yxati) ============
async def _show_channels(target: Message):
    rows = get_channels()
    if rows:
        text = "📢 Reklama kanallari:\n" + "\n".join(f"• {title}" for _id, _chat, title in rows)
    else:
        text = "📢 Hali reklama kanali qo'shilmagan."
    text += "\n\nReklama qilganingizda shu kanallardan birini tanlaysiz. Bot kanallarda admin bo'lishi kerak."
    buttons = [[InlineKeyboardButton(text="➕ Kanal qo'shish", callback_data="pc:add")]]
    for cid, _chat, title in rows:
        buttons.append([InlineKeyboardButton(text=f"🗑 {title}", callback_data=f"pc:del:{cid}")])
    await target.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.message(F.text == "📢 Kanal reklama")
async def btn_ad_channel(message: Message):
    if not is_admin(message.from_user.id):
        return
    await _show_channels(message)


@router.callback_query(F.data == "pc:add")
async def cb_pc_add(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await callback.message.answer(
        "➕ Kanal qo'shish: kanaldan istalgan postni shu yerga forward qiling, "
        "yoki kanal username'ini (@kanal_nomi) yoki ID raqamini yuboring.\n"
        "Bot kanalda admin bo'lishi kerak."
    )
    await state.set_state(AdminFSM.waiting_addchannel)
    await callback.answer()


@router.message(StateFilter(AdminFSM.waiting_addchannel))
async def process_addchannel(message: Message, state: FSMContext):
    await state.clear()
    chat = getattr(message, "forward_from_chat", None)
    if not chat:
        chat = getattr(getattr(message, "forward_origin", None), "chat", None)
    if not chat:
        raw = (message.text or "").strip()
        for prefix in ("https://t.me/", "http://t.me/", "t.me/"):
            if raw.startswith(prefix):
                raw = raw[len(prefix):]
        raw = raw.strip("/ ")
        if not raw:
            await message.answer("❗️ Kanaldan post forward qiling, yoki @username / ID yuboring.")
            return
        ref = int(raw) if re.fullmatch(r"-?\d+", raw) else (raw if raw.startswith("@") else "@" + raw)
        try:
            chat = await bot.get_chat(ref)
        except Exception as e:
            logging.error(e)
            await message.answer("❗️ Kanal topilmadi. Username/ID to'g'riligini va bot kanalga qo'shilganini tekshiring.")
            return
    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(chat.id, me.id)
        is_adm = member.status in ("administrator", "creator")
    except Exception as e:
        logging.error(e)
        is_adm = False
    if not is_adm:
        await message.answer("⚠️ Bot bu kanalda admin emas. Avval botni kanalga admin qiling, keyin qayta qo'shing.")
        return
    title = chat.title or (f"@{chat.username}" if chat.username else str(chat.id))
    db("INSERT OR REPLACE INTO post_channels (id, chat_id, title) VALUES "
       "((SELECT id FROM post_channels WHERE chat_id=?), ?, ?)", (chat.id, chat.id, title))
    if chat.username and not get_setting("ad_channel", ""):
        set_setting("ad_channel", f"@{chat.username}")
    await message.answer(f"✅ \"{title}\" reklama kanallariga qo'shildi.")
    await _show_channels(message)


@router.callback_query(F.data.startswith("pc:del:"))
async def cb_pc_del(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    try:
        cid = int(callback.data.split(":")[2])
    except (ValueError, IndexError):
        await callback.answer("⚠️ Bajarilmadi.", show_alert=True)
        return
    db("DELETE FROM post_channels WHERE id=?", (cid,))
    await callback.message.answer("🗑 Kanal ro'yxatdan olib tashlandi.")
    await callback.answer()


# ============ ADMIN: VIP BERISH VA XABAR ============
@router.message(Command("givevip"))
async def cmd_givevip(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    if not command.args:
        await message.answer("❗️ Foydalanish: /givevip USER_ID MUDDAT (1hafta/1oy/3oy/5oy/7oy/8oy/forever)")
        return
    try:
        parts = command.args.split()
        target_id = int(parts[0])
        duration = parts[1]
    except (ValueError, IndexError):
        await message.answer("❗️ Foydalanish: /givevip USER_ID MUDDAT")
        return
    if duration == "forever":
        days = None
    elif duration in MONTH_TO_DAYS:
        days = MONTH_TO_DAYS[duration]
    else:
        await message.answer("❗️ Noto'g'ri muddat. Variantlar: 1hafta, 1oy, 3oy, 5oy, 7oy, 8oy yoki forever")
        return
    grant_vip(target_id, duration)
    await message.answer(f"✅ {target_id} foydalanuvchiga VIP berildi ({duration}).")
    try:
        await bot.send_message(target_id, "🎉 Tabriklaymiz! Sizga VIP faollashtirildi.")
    except Exception:
        pass


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    if not command.args:
        await message.answer("❗️ Foydalanish: /broadcast Xabar matni")
        return
    ids = [r[0] for r in db("SELECT user_id FROM users", (), "all")]
    sent, failed = 0, 0
    status = await message.answer(f"Yuborilmoqda... 0/{len(ids)}")
    for i, uid in enumerate(ids, start=1):
        try:
            await bot.send_message(uid, command.args)
            sent += 1
        except Exception:
            failed += 1
        if i % 25 == 0:
            await status.edit_text(f"Yuborilmoqda... {i}/{len(ids)}")
        await asyncio.sleep(0.05)
    await status.edit_text(f"✅ Yakunlandi. Yuborildi: {sent}, xato: {failed}")


# ============ 🎞 FASL QO'SHISH / 🗑 FASL OLISH (raqam bilan) ============
def _seasons_text(code: str) -> str:
    seasons = get_seasons(code)
    return ", ".join(str(x) for x in seasons) if seasons else "yo'q"


def _parse_code_season(text):
    parts = (text or "").split()
    if len(parts) != 2:
        return None
    try:
        season = int(parts[1])
    except ValueError:
        return None
    if season < 1:
        return None
    return parts[0], season


@router.message(F.text == "📥 Fasl qo'shish")
async def btn_add_season(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "📥 Fasl qo'shish uchun animening kodini va qo'shmoqchi bo'lgan fasl raqamini yuboring "
        "(KOD FASL).\nIstalgan fasl raqamini yozishingiz mumkin."
    )
    await state.set_state(AdminFSM.waiting_addseason)


@router.message(StateFilter(AdminFSM.waiting_addseason))
async def process_add_season(message: Message, state: FSMContext):
    await state.clear()
    parsed = _parse_code_season(message.text)
    if not parsed:
        await message.answer("❗️ Format: KOD FASL. Qaytadan \"📥 Fasl qo'shish\" ni bosing.")
        return
    code, season = parsed
    row = db("SELECT title FROM animes WHERE code=?", (code,), "one")
    if not row:
        await message.answer("❌ Bunday kodli anime topilmadi.")
        return
    title = row[0]
    if season in get_seasons(code):
        await message.answer(f"⚠️ \"{title}\" da {season}-fasl allaqachon bor.\n🎞 Hozirgi fasllar: {_seasons_text(code)}")
        return
    db("INSERT OR IGNORE INTO seasons (anime_code, season_number) VALUES (?,?)", (code, season))
    sync_total_seasons(code)
    await message.answer(
        f"✅ \"{title}\" ga {season}-fasl qo'shildi.\n"
        f"🎞 Hozirgi fasllar: {_seasons_text(code)}\n\n"
        f"Qismlarini yuklash:\n/addep {code} {season} 1"
    )


@router.message(F.text == "📤 Fasl olish")
async def btn_del_season(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "📤 Fasl olish uchun animening kodini va o'chirmoqchi bo'lgan fasl raqamini yuboring "
        "(KOD FASL).\nIstalgan faslni olishingiz mumkin, faqat o'sha fasl o'chadi, qolganlari joyida qoladi."
    )
    await state.set_state(AdminFSM.waiting_delseason)


@router.message(StateFilter(AdminFSM.waiting_delseason))
async def process_del_season(message: Message, state: FSMContext):
    await state.clear()
    parsed = _parse_code_season(message.text)
    if not parsed:
        await message.answer("❗️ Format: KOD FASL. Qaytadan \"📤 Fasl olish\" ni bosing.")
        return
    code, season = parsed
    row = db("SELECT title FROM animes WHERE code=?", (code,), "one")
    if not row:
        await message.answer("❌ Bunday kodli anime topilmadi.")
        return
    title = row[0]
    seasons = get_seasons(code)
    if season not in seasons:
        await message.answer(f"⚠️ \"{title}\" da {season}-fasl yo'q.\n🎞 Mavjud fasllar: {_seasons_text(code)}")
        return
    if len(seasons) <= 1:
        await message.answer("⚠️ Bu animeda faqat 1 ta fasl bor. Butunlay o'chirish uchun "
                             "\"📤 Anime olish\" dan foydalaning.")
        return
    eps = db("SELECT COUNT(*) FROM episodes WHERE anime_code=? AND season_number=?",
             (code, season), "one")[0]
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Ha, o'chirish", callback_data=f"delseason:{code}:{season}"),
        InlineKeyboardButton(text="❌ Yo'q", callback_data="adm:keep"),
    ]])
    await message.answer(
        f"❓ \"{title}\" ning {season}-fasli va undagi {eps} ta qism o'chiriladi.\n"
        f"Qolgan fasllar: {', '.join(str(x) for x in seasons if x != season)}\n\nRozimisiz?",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("delseason:"))
async def cb_del_season(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    try:
        code, season = callback.data[len("delseason:"):].rsplit(":", 1)
        season = int(season)
    except ValueError:
        await callback.answer("⚠️ Bajarilmadi.", show_alert=True)
        return
    row = db("SELECT title FROM animes WHERE code=?", (code,), "one")
    seasons = get_seasons(code)
    if not row or season not in seasons or len(seasons) <= 1:
        await callback.answer("⚠️ Bajarilmadi.", show_alert=True)
        return
    db("DELETE FROM episodes WHERE anime_code=? AND season_number=?", (code, season))
    db("DELETE FROM history WHERE anime_code=? AND season_number=?", (code, season))
    db("DELETE FROM seasons WHERE anime_code=? AND season_number=?", (code, season))
    sync_total_seasons(code)
    await callback.message.answer(
        f"🗑 \"{row[0]}\" ning {season}-fasli o'chirildi.\n🎞 Qolgan fasllar: {_seasons_text(code)}"
    )
    await callback.answer()


# ============ 💎 VIP BOSHQARUVI (berish / olish / ro'yxat) ============
PLAN_LABELS = {
    "forever": "♾ Cheksiz VIP", "1hafta": "📅 1 haftalik VIP", "1oy": "📅 1 oylik VIP",
    "3oy": "📅 3 oylik VIP", "5oy": "📅 5 oylik VIP", "7oy": "📅 7 oylik VIP", "8oy": "📅 8 oylik VIP",
}
PLAN_ORDER = ["forever", "1hafta", "1oy", "3oy", "5oy", "7oy", "8oy"]


def grant_vip(user_id: int, plan: str):
    """plan: 1oy / 3oy / 5oy / 8oy / forever. Oylik VIP hali tugamagan bo'lsa, yangi muddat uning ustiga qo'shiladi."""
    ensure_user(user_id, "")
    if plan == "forever":
        until = "forever"
    else:
        base = datetime.now()
        row = db("SELECT is_vip, vip_until FROM users WHERE user_id=?", (user_id,), "one")
        if row and row[0] and row[1] and row[1] != "forever":
            try:
                cur_end = datetime.fromisoformat(row[1])
                if cur_end > base:
                    base = cur_end
            except ValueError:
                pass
        until = (base + timedelta(days=MONTH_TO_DAYS[plan])).isoformat()
    db("UPDATE users SET is_vip=1, vip_until=?, vip_plan=? WHERE user_id=?", (until, plan, user_id))
    return until


def revoke_vip(user_id: int):
    db("UPDATE users SET is_vip=0, vip_until=NULL, vip_plan=NULL WHERE user_id=?", (user_id,))


def parse_plan(text: str):
    t = re.sub(r"\s+", "", (text or "").lower())
    if t in ("forever", "cheksiz", "abadiy", "umrbod"):
        return "forever"
    if t in ("1hafta", "1haftalik", "hafta", "haftalik", "7kun"):
        return "1hafta"
    m = re.fullmatch(r"(\d+)(oy|oylik)?", t)
    if m and f"{int(m.group(1))}oy" in MONTH_TO_DAYS:
        return f"{int(m.group(1))}oy"
    return None


def fmt_vip_until(until) -> str:
    if until == "forever":
        return "cheksiz"
    try:
        d = datetime.fromisoformat(until)
    except (TypeError, ValueError):
        return "noma'lum"
    left = max((d - datetime.now()).days, 0)
    return f"{d:%Y-%m-%d} gacha ({left} kun qoldi)"


async def user_label(uid: int, username: str) -> str:
    """Foydalanuvchini ID emas, username (yoki ismi) bilan ko'rsatadi."""
    if username:
        return f"@{username}"
    try:
        chat = await bot.get_chat(uid)
        if chat.username:
            db("UPDATE users SET username=? WHERE user_id=?", (chat.username, uid))
            return f"@{chat.username}"
        name = " ".join(x for x in (chat.first_name, chat.last_name) if x)
        if name:
            return name
    except Exception:
        pass
    return f"ID {uid}"


def active_vips():
    """Hozir amalda bo'lgan VIPlar: [(user_id, username, until, plan)]. Muddati o'tganlar tozalanadi."""
    rows = db("SELECT user_id, username, vip_until, vip_plan FROM users WHERE is_vip=1", (), "all")
    out = []
    for uid, uname, until, plan in rows:
        if until and until != "forever":
            try:
                if datetime.fromisoformat(until) < datetime.now():
                    revoke_vip(uid)
                    continue
            except ValueError:
                pass
        if until == "forever" and not plan:
            plan = "forever"
        out.append((uid, uname or "", until, plan))
    return out


def resolve_user(target: str):
    t = target.strip()
    if re.fullmatch(r"\d+", t):
        row = db("SELECT username FROM users WHERE user_id=?", (int(t),), "one")
        return int(t), (row[0] if row and row[0] else "")
    row = db("SELECT user_id, username FROM users WHERE lower(username)=?", (t.lstrip("@").lower(),), "one")
    return (row[0], row[1] or "") if row else None


async def _give_vip_and_report(target: Message, uid: int, plan: str):
    until = grant_vip(uid, plan)
    row = db("SELECT username FROM users WHERE user_id=?", (uid,), "one")
    label = await user_label(uid, row[0] if row else "")
    await target.answer(f"✅ {label} ga {PLAN_LABELS[plan]} berildi.\n⏳ {fmt_vip_until(until)}")
    try:
        await bot.send_message(uid, "🎉 Tabriklaymiz! Sizga VIP faollashtirildi.")
    except Exception:
        pass


@router.message(F.text == "💎 VIP berish")
async def btn_vip_give(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "💎 VIP berish: foydalanuvchining ID raqami yoki @username'ini va muddatni yozing (ID MUDDAT).\n"
        "Muddatlar: 1hafta, 1oy, 3oy, 5oy, 7oy, 8oy yoki cheksiz.\n\n"
        "Muddatni yozmasangiz, tanlash uchun tugmalar chiqadi.\n"
        "(@username bilan berish uchun foydalanuvchi botga /start bosgan bo'lishi kerak.)"
    )
    await state.set_state(AdminFSM.waiting_vipgive)


@router.message(StateFilter(AdminFSM.waiting_vipgive))
async def process_vip_give(message: Message, state: FSMContext):
    await state.clear()
    parts = (message.text or "").split()
    if not parts:
        await message.answer("❗️ Format: ID MUDDAT")
        return
    resolved = resolve_user(parts[0])
    if not resolved:
        await message.answer("❌ Bu username botda topilmadi. Foydalanuvchi botga /start bosganmi? Yoki ID raqami bilan yuboring.")
        return
    uid, uname = resolved
    if len(parts) > 1:
        plan = parse_plan("".join(parts[1:]))
        if not plan:
            await message.answer("❗️ Muddat: 1hafta, 1oy, 3oy, 5oy, 7oy, 8oy yoki cheksiz.")
            return
        await _give_vip_and_report(message, uid, plan)
        return
    label = await user_label(uid, uname)
    buttons = [[InlineKeyboardButton(text=PLAN_LABELS[k].replace("📅 ", ""), callback_data=f"vg:{uid}:{k}")]
               for k in PLAN_ORDER]
    await message.answer(f"💎 {label} ga qancha muddatga VIP beramiz?",
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("vg:"))
async def cb_vip_give(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    try:
        _, uid, plan = callback.data.split(":")
        uid = int(uid)
    except ValueError:
        await callback.answer("⚠️ Bajarilmadi.", show_alert=True)
        return
    if plan != "forever" and plan not in MONTH_TO_DAYS:
        await callback.answer("⚠️ Bajarilmadi.", show_alert=True)
        return
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await _give_vip_and_report(callback.message, uid, plan)
    await callback.answer()


@router.message(F.text == "🚫 VIP olish")
async def btn_vip_take(message: Message):
    if not is_admin(message.from_user.id):
        return
    vips = active_vips()
    if not vips:
        await message.answer("ℹ️ Hozircha VIP foydalanuvchilar yo'q.")
        return
    buttons = []
    for uid, uname, until, plan in vips[:90]:
        label = await user_label(uid, uname)
        buttons.append([InlineKeyboardButton(text=label[:60], callback_data=f"vr:{uid}")])
    note = "\n(Birinchi 90 tasi ko'rsatildi.)" if len(vips) > 90 else ""
    await message.answer("🚫 VIP olmoqchi bo'lgan foydalanuvchini tanlang:" + note,
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("vr:"))
async def cb_vip_take_ask(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    uid = int(callback.data.split(":")[1])
    row = db("SELECT username, vip_until, vip_plan FROM users WHERE user_id=? AND is_vip=1", (uid,), "one")
    if not row:
        await callback.answer("Bu foydalanuvchida VIP yo'q.", show_alert=True)
        return
    uname, until, plan = row
    if until == "forever" and not plan:
        plan = "forever"
    label = await user_label(uid, uname or "")
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Ha, olinsin", callback_data=f"vry:{uid}"),
        InlineKeyboardButton(text="❌ Yo'q, olinmasin", callback_data="vrn"),
    ]])
    await callback.message.answer(
        f"🚫 Bu foydalanuvchidan VIP olinsinmi?\n\n👤 {label}\n"
        f"💎 {PLAN_LABELS.get(plan, 'VIP')}\n⏳ {fmt_vip_until(until)}",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("vry:"))
async def cb_vip_take_yes(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    uid = int(callback.data.split(":")[1])
    row = db("SELECT username FROM users WHERE user_id=?", (uid,), "one")
    label = await user_label(uid, row[0] if row and row[0] else "")
    revoke_vip(uid)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer(f"✅ {label} dan VIP olindi.")
    await callback.answer()


@router.callback_query(F.data == "vrn")
async def cb_vip_take_no(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer("👌 VIP olinmadi.")
    await callback.answer()


@router.message(F.text == "📋 VIPlar ro'yxati")
async def btn_vip_list(message: Message):
    if not is_admin(message.from_user.id):
        return
    vips = active_vips()
    groups = {k: [] for k in PLAN_ORDER}
    groups["other"] = []
    for uid, uname, until, plan in vips:
        groups[plan if plan in PLAN_LABELS else "other"].append((uid, uname, until))
    lines = [f"💎 VIP ro'yxati (jami: {len(vips)})"]
    for key in PLAN_ORDER + ["other"]:
        items = groups[key]
        if key == "other" and not items:
            continue
        title = PLAN_LABELS.get(key, "💎 Boshqa VIP")
        lines.append(f"\n{title} ({len(items)}):")
        if not items:
            lines.append("—")
        for uid, uname, until in items:
            label = await user_label(uid, uname)
            lines.append(f"• {label}" + ("" if key == "forever" else f" — {fmt_vip_until(until)}"))
    chunk = ""
    for line in lines:
        if len(chunk) + len(line) + 1 > 3800:
            await message.answer(chunk)
            chunk = ""
        chunk += line + "\n"
    if chunk.strip():
        await message.answer(chunk)


# ============ ✏️ TAG TAHRIRLASH ============
def find_animes(query: str):
    q = query.strip()
    exact = db("SELECT code, title FROM animes WHERE code=?", (q,), "one")
    if exact:
        return [exact]
    return db("SELECT code, title FROM animes WHERE title LIKE ? OR custom_caption LIKE ? LIMIT 20",
              (f"%{q}%", f"%{q}%"), "all")


def build_tag_template(code: str) -> str:
    row = db("SELECT title, genre, channel_name, is_premium, custom_caption FROM animes WHERE code=?",
             (code,), "one")
    title, genre, channel, is_premium, custom = row
    if custom:
        body = render_qismi(custom, count_episodes(code))
    else:
        lines = [f"Nomi: {title}", f"Fasllar: {len(get_seasons(code))}", f"Qismi: {count_episodes(code)}"]
        if genre and genre != "-":
            lines.append(f"Janr: {genre}")
        ch = channel or get_setting("ad_channel", "")
        if ch:
            lines.append(f"Kanal: {ch}")
        body = "\n".join(lines)
    return body + f"\nPremium: {'ha' if is_premium else 'yoq'}"


async def _begin_edit_tag(target: Message, state: FSMContext, code: str):
    row = db("SELECT title FROM animes WHERE code=?", (code,), "one")
    if not row:
        await target.answer("❌ Anime topilmadi.")
        return
    await state.set_state(AdminFSM.waiting_edittag_text)
    await state.update_data(code=code)
    await target.answer(
        f"✏️ \"{row[0]}\" (kod: {code}) hozirgi tagi:\n\n"
        f"<code>{_html.escape(build_tag_template(code))}</code>\n\n"
        "Shu matnni nusxalab, kerakli joyini o'zgartirib yuboring "
        "(nom, fasllar, qismi, premium va boshqalar). Premium: ha yoki yo'q.\n"
        "Bekor qilish: /start",
        parse_mode="HTML",
    )


@router.message(F.text == "✏️ Tag tahrirlash")
async def btn_edit_tag(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await message.answer("✏️ Tagini tahrirlamoqchi bo'lgan animening nomini yoki kodini yozing:")
    await state.set_state(AdminFSM.waiting_edittag_find)


@router.message(StateFilter(AdminFSM.waiting_edittag_find))
async def process_edittag_find(message: Message, state: FSMContext):
    q = (message.text or "").strip()
    if not q:
        return
    matches = find_animes(q)
    if not matches:
        await message.answer("❌ Bunday anime topilmadi. Nom yoki kodni qaytadan yozing.")
        return
    if len(matches) == 1:
        await _begin_edit_tag(message, state, matches[0][0])
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{title} ({code})"[:60], callback_data=f"etp:{code}")]
        for code, title in matches
    ])
    await message.answer("Qaysi anime tagini tahrirlaymiz?", reply_markup=kb)


@router.callback_query(F.data.startswith("etp:"))
async def cb_edittag_pick(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await _begin_edit_tag(callback.message, state, callback.data.split(":", 1)[1])
    await callback.answer()


@router.message(StateFilter(AdminFSM.waiting_edittag_text))
async def process_edittag_text(message: Message, state: FSMContext):
    data = await state.get_data()
    code = data.get("code")
    row = db("SELECT poster_file_id, is_premium FROM animes WHERE code=?", (code,), "one") if code else None
    if not row:
        await state.clear()
        await message.answer("⚠️ Anime topilmadi, qaytadan boshlang.")
        return
    poster_file_id, cur_premium = row
    control, free = _parse_anime_caption(message.text or "")
    if not free:
        await message.answer("❗️ Tag bo'sh bo'lmasin. Qayta yuboring.")
        return
    if len(free) > 1000:
        await message.answer(f"❗️ Tag juda uzun ({len(free)} belgi). 1000 belgidan oshmasin.")
        return
    if control.get("premium"):
        premium = _parse_premium(control["premium"])
        if premium is None:
            await message.answer("❌ Premium qiymati tushunarsiz. Yozing: Premium: ha  yoki  Premium: yo'q")
            return
    else:
        premium = cur_premium
    first_line = next((ln.strip() for ln in free.split("\n") if ln.strip()), "")
    title = (control.get("nomi") or first_line or code)[:100]
    await state.update_data(new_title=title, new_caption=free, new_premium=premium)
    await state.set_state(AdminFSM.waiting_edittag_confirm)

    plain_preview = render_qismi(free, count_episodes(code))
    preview = fit_quotes(plain_preview, 1024)
    try:
        if poster_file_id:
            await message.answer_photo(poster_file_id, caption=preview, parse_mode="HTML")
        else:
            await message.answer(preview, parse_mode="HTML")
    except TelegramBadRequest:
        await message.answer(plain_preview)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Ha, bo'ldi", callback_data="et:ok"),
        InlineKeyboardButton(text="❌ Yo'q, tahrirlamayman", callback_data="et:no"),
    ]])
    await message.answer(
        f"🔎 Qidiruvdagi nomi: {title}\n💎 Premium: {'ha' if premium else 'yoq'}\n\n"
        "Tag shu holatda saqlansinmi?",
        reply_markup=kb,
    )


@router.callback_query(F.data == "et:ok")
async def cb_edittag_ok(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    data = await state.get_data()
    code, caption = data.get("code"), data.get("new_caption")
    if not code or not caption:
        await callback.answer("⚠️ Sessiya tugagan, qaytadan boshlang.", show_alert=True)
        return
    db("UPDATE animes SET title=?, is_premium=?, custom_caption=? WHERE code=?",
       (data["new_title"], data["new_premium"], caption, code))
    await state.clear()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer(f"✅ {code}-anime tagi tahrirlandi!")
    await callback.answer()


@router.callback_query(F.data == "et:no")
async def cb_edittag_no(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer("👌 Tahrirlanmadi, tag o'zgarishsiz qoldi.")
    await callback.answer()


# ============ 📒 ANIME NEWS QO'SHISH (admin) ============
@router.callback_query(F.data == "na:new")
async def cb_news_new_date(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await _ask_news_date(callback.message, state)
    await callback.answer()


@router.message(StateFilter(AdminFSM.waiting_news_date))
async def process_news_date(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    label = " ".join((message.text or "").split())
    if not label:
        await message.answer("❗️ Kunni matn bilan yozing. Masalan: 1 okt")
        return
    if len(label) > 30:
        await message.answer("❗️ Kun nomi juda uzun (30 belgigacha). Masalan: 1 okt")
        return
    existing = db("SELECT id FROM news_dates WHERE lower(label)=lower(?)", (label,), "one")
    if not existing:
        db("INSERT INTO news_dates (label, sort_key, created) VALUES (?,?,?)",
           (label, parse_news_date(label), datetime.now().isoformat()))
    await state.clear()
    await message.answer(
        f"✅ \"{label}\" kuni 📒 Anime news bo'limiga qo'shildi.\n\n"
        "Endi shu kunga anime qo'shish uchun kunni tanlang 👇",
        reply_markup=news_admin_dates_kb("na", with_new=True),
    )


@router.callback_query(F.data.startswith("na:d:"))
async def cb_news_pick_date(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    try:
        did = int(callback.data.split(":")[2])
    except ValueError:
        await callback.answer()
        return
    d = db("SELECT label FROM news_dates WHERE id=?", (did,), "one")
    if not d:
        await callback.answer("⚠️ Kun topilmadi.", show_alert=True)
        return
    await state.set_state(AdminFSM.waiting_news_body)
    await state.update_data(news_date=did)
    await callback.message.answer(
        f"📅 {d[0]} kuni chiqadigan animeni yozing.\n\n"
        "• Xohlagancha yozing: nomi, nechanchi fasl/qism, qachon chiqishi, janri va hokazo.\n"
        "• Rasm bilan yuborsangiz — rasm ham chiqadi (yozuvni rasm tagiga yozing).\n"
        "• Birinchi qator tugma nomi bo'ladi (masalan: Solo Leveling 3-fasl).\n"
        "• Bo'sh qator bilan ajratilgan har bir blok alohida iqtibosda chiqadi.\n\n"
        "Bekor qilish: /start"
    )
    await callback.answer()


@router.message(StateFilter(AdminFSM.waiting_news_body))
async def process_news_body(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    text = ((message.caption if message.photo else message.text) or "").strip()
    if not text:
        await message.answer("❗️ Anime haqida yozing (rasm yuborsangiz, yozuvni rasm tagiga yozing).")
        return
    if len(text) > 3500:
        await message.answer(f"❗️ Matn juda uzun ({len(text)} belgi). 3500 belgidan oshmasin.")
        return
    photo = message.photo[-1].file_id if message.photo else None
    title = next((ln.strip() for ln in text.split("\n") if ln.strip()), "Anime")[:60]
    data = await state.get_data()
    d = db("SELECT label FROM news_dates WHERE id=?", (data.get("news_date"),), "one")
    if not d:
        await state.clear()
        await message.answer("⚠️ Kun topilmadi, \"📒 Anime news\" ni qaytadan bosing.")
        return
    await state.update_data(news_title=title, news_body=text, news_photo=photo)
    await state.set_state(AdminFSM.waiting_news_confirm)
    await send_rich(message.chat.id, photo, news_post_html(d[0], text))
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        ibtn("📥 Ha, qo'shaman", "nc:yes", style="success"),
        ibtn("📤 Yo'q, qo'shmayman", "nc:no", style="danger"),
    ]])
    await message.answer(
        f"📒 \"{title}\" — {d[0]} kuniga qo'shilsinmi?\n"
        "Ha desangiz, botdagi 📒 Anime news bo'limiga qo'shiladi va kanalga avtomatik yuboriladi.",
        reply_markup=kb,
    )


@router.callback_query(F.data == "nc:no")
async def cb_news_no(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer("👌 Qo'shilmadi.")
    await callback.answer()


@router.callback_query(F.data == "nc:yes")
async def cb_news_yes(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    data = await state.get_data()
    did, title, body, photo = (data.get("news_date"), data.get("news_title"),
                               data.get("news_body"), data.get("news_photo"))
    d = db("SELECT label FROM news_dates WHERE id=?", (did,), "one") if did else None
    if not d or not body:
        await callback.answer("⚠️ Sessiya tugagan, qaytadan boshlang.", show_alert=True)
        return
    await state.clear()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    db("INSERT INTO news_items (date_id, title, body, photo_file_id, created) VALUES (?,?,?,?,?)",
       (did, title, body, photo, datetime.now().isoformat()))
    await callback.message.answer(f"✅ \"{title}\" 📒 Anime news bo'limiga qo'shildi!")

    channels = get_channels()
    if not channels:
        await callback.message.answer("ℹ️ Kanal qo'shilmagani uchun kanalga yuborilmadi "
                                      "(\"📢 Kanal reklama\" orqali kanal qo'shing).")
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            ibtn("📒 Anime news", url=f"https://t.me/{BOT_USERNAME}?start=news", style="success")
        ]])
        html_text = news_post_html(d[0], body)
        for _cid, chat_id, ch_title in channels:
            try:
                await post_to_channel(chat_id, photo, html_text, kb)
                await callback.message.answer(f"📢 \"{ch_title}\" kanaliga yuborildi.")
            except Exception as e:
                logging.error(e)
                await callback.message.answer(channel_error_text(e))
    await callback.answer()


# ============ 🚫 NEWS DELETE (admin) ============
async def _news_delete_list(target: Message, did: int, edit: CallbackQuery = None):
    d = db("SELECT label FROM news_dates WHERE id=?", (did,), "one")
    if not d:
        await target.answer("⚠️ Kun topilmadi.")
        return
    items = news_items(did)
    if items:
        lines = "\n".join(f"{n}. {esc(t)}" for n, (_i, t) in enumerate(items, start=1))
        text = quote(f"📅 <b>{esc(d[0])}</b> kuni qo'shilgan animelar:\n\n{lines}") + \
            "\n\n👇 Olib tashlamoqchi bo'lgan animeni tanlang"
    else:
        text = quote(f"📅 <b>{esc(d[0])}</b> kunida anime yo'q.")
    rows = [[ibtn(f"{n}. {t}"[:60], f"nx:i:{i}", style="primary")] for n, (i, t) in enumerate(items, start=1)]
    if not items:
        rows.append([ibtn("🗑 Kunni olib tashlash", f"nx:dd:{did}", style="danger")])
    rows.append([ibtn("🔙 Orqaga", "nx:b", style="danger")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    if edit:
        await edit_view(edit, text, kb, prefer_text=True)
    else:
        await target.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("nx:"))
async def cb_news_delete(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    parts = callback.data.split(":")
    action = parts[1]
    try:
        arg = int(parts[2]) if len(parts) > 2 else None
    except ValueError:
        await callback.answer()
        return

    if action == "b":
        if not news_dates():
            await edit_view(callback, "ℹ️ Anime news bo'limida hozircha hech narsa yo'q.", None)
        else:
            await edit_view(callback, "🚫 News delete — qaysi kundagi animeni olib tashlaymiz?",
                            news_admin_dates_kb("nx", with_new=False))
    elif action == "d":
        await _news_delete_list(callback.message, arg, edit=callback)
    elif action == "i":
        item = db("SELECT title, date_id FROM news_items WHERE id=?", (arg,), "one")
        if not item:
            await callback.answer("⚠️ Topilmadi (allaqachon olib tashlangan).", show_alert=True)
            return
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            ibtn("✅ Tugadi (olib tashla)", f"nx:y:{arg}", style="danger"),
            ibtn("❌ Yo'q, olma", f"nx:d:{item[1]}", style="success"),
        ]])
        await edit_view(callback, quote(f"🎬 <b>{esc(item[0])}</b>\n\nBu anime chiqib bo'ldimi? "
                                        "\"Tugadi\" bosilsa botdagi 📒 Anime news dan olib tashlanadi."),
                        kb, prefer_text=True)
    elif action == "y":
        item = db("SELECT title, date_id FROM news_items WHERE id=?", (arg,), "one")
        if not item:
            await callback.answer("⚠️ Allaqachon olib tashlangan.", show_alert=True)
            return
        db("DELETE FROM news_items WHERE id=?", (arg,))
        did = item[1]
        if not news_items(did):
            db("DELETE FROM news_dates WHERE id=?", (did,))
        await callback.message.answer(f"✅ \"{item[0]}\" 📒 Anime news dan olib tashlandi.")
        if news_dates():
            await edit_view(callback, "🚫 News delete — yana qaysi kundagi animeni olib tashlaymiz?",
                            news_admin_dates_kb("nx", with_new=False))
        else:
            await edit_view(callback, "ℹ️ Anime news bo'limida endi hech narsa qolmadi.", None)
    elif action == "dd":
        if not news_items(arg):
            db("DELETE FROM news_dates WHERE id=?", (arg,))
            await callback.message.answer("✅ Kun olib tashlandi.")
        if news_dates():
            await edit_view(callback, "🚫 News delete — qaysi kundagi animeni olib tashlaymiz?",
                            news_admin_dates_kb("nx", with_new=False))
        else:
            await edit_view(callback, "ℹ️ Anime news bo'limida hech narsa yo'q.", None)
    await callback.answer()


# ============ 💎 VIP TO'LOV CHEKI ============
@router.message(F.photo | F.document)
async def receive_receipt(message: Message, state: FSMContext):
    uid = message.from_user.id
    plan_key = get_pending_plan(uid)
    if not plan_key:
        if not is_admin(uid):
            await message.answer("💎 Avval /start bosib, 💎 VIP olish bo'limidan tarifni tanlang, so'ng chekni yuboring.")
        return
    if message.document:
        mime = message.document.mime_type or ""
        if not (mime.startswith("image/") or mime == "application/pdf"):
            await message.answer("❗️ Chekni rasm (skrinshot) ko'rinishida yuboring 📃")
            return
    plan = next((p for p in VIP_PLANS if p[0] == plan_key), None)
    if not plan:
        PENDING_PLAN.pop(uid, None)
        return
    _key, label, price = plan

    file_unique_id = message.photo[-1].file_unique_id if message.photo else message.document.file_unique_id
    prev = db("SELECT status FROM vip_receipts WHERE file_unique_id=? AND status IN ('pending','approved') LIMIT 1",
              (file_unique_id,), "one")
    db("INSERT INTO vip_receipts (user_id, plan, file_unique_id, status, created) VALUES (?,?,?,?,?)",
       (uid, plan_key, file_unique_id, "pending", datetime.now().isoformat()))
    rid = db("SELECT MAX(id) FROM vip_receipts WHERE user_id=?", (uid,), "one")[0]

    u = message.from_user
    uname = f"@{u.username}" if u.username else "yo'q"
    cap = (
        "💎 <b>Yangi VIP to'lov cheki</b>\n\n"
        f"👤 Foydalanuvchi: <a href=\"tg://user?id={uid}\">{esc(u.full_name or 'Nomalum')}</a>\n"
        f"🔗 Username: {esc(uname)}\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"📦 Tarif: <b>{label}</b>\n"
        f"💵 Narxi: <b>{price}</b>\n"
        f"🕒 Vaqt: {datetime.now():%Y-%m-%d %H:%M}"
    )
    if prev:
        cap += "\n\n⚠️ <b>Diqqat:</b> bu chek rasmi avval ham yuborilgan! Ehtiyot bo'ling."
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        ibtn("✅ VIP berilsin", f"rc:ok:{rid}", style="success"),
        ibtn("❌ Berilmasin", f"rc:no:{rid}", style="danger"),
    ]])
    try:
        await message.copy_to(RECEIPT_ADMIN_ID, caption=cap, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        logging.error(f"Chekni adminga yuborib bo'lmadi: {e}")
        db("UPDATE vip_receipts SET status='failed' WHERE id=?", (rid,))
        await message.answer(f"⚠️ Chekni adminga yetkazib bo'lmadi. Iltimos, chekni {ADMIN_USERNAME} ga yuboring.")
        return
    PENDING_PLAN.pop(uid, None)
    await message.answer(
        "✅ Chekingiz adminga yuborildi📤\n"
        "Admin to'lovni tasdiqlagach, sizga botda VIP beriladi😊\n"
        "Bu bir necha daqiqa vaqt🕝 olishi mumkin.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[ibtn("🏠 Bosh sahifa", "home")]]),
    )


@router.callback_query(F.data.startswith("rc:"))
async def cb_receipt(callback: CallbackQuery):
    uid = callback.from_user.id
    if not (is_admin(uid) or uid == RECEIPT_ADMIN_ID):
        await callback.answer("⛔️ Ruxsat yo'q", show_alert=True)
        return
    try:
        _, action, rid = callback.data.split(":")
        rid = int(rid)
    except ValueError:
        await callback.answer("⚠️ Bajarilmadi.", show_alert=True)
        return
    row = db("SELECT user_id, plan, status FROM vip_receipts WHERE id=?", (rid,), "one")
    if not row:
        await callback.answer("⚠️ Chek topilmadi.", show_alert=True)
        return
    target, plan, status = row
    if status != "pending":
        await callback.answer("ℹ️ Bu chek allaqachon ko'rib chiqilgan.", show_alert=True)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return
    label = PLAN_LABELS.get(plan, plan)
    if action == "ok":
        db("UPDATE vip_receipts SET status='approved' WHERE id=?", (rid,))
        until = grant_vip(target, plan)
        result = f"✅ VIP berildi: {label}\n⏳ {fmt_vip_until(until)}"
        notify = (f"🎉 Tabriklaymiz! To'lovingiz tasdiqlandi, sizga {label} faollashtirildi.\n"
                  f"⏳ {fmt_vip_until(until)}")
    else:
        db("UPDATE vip_receipts SET status='rejected' WHERE id=?", (rid,))
        result = "❌ VIP berilmadi (rad etildi)."
        notify = (f"❌ Afsuski to'lovingiz tasdiqlanmadi.\n"
                  f"📥 Murojaat uchun: {ADMIN_USERNAME}")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer(f"{result}\n🆔 Foydalanuvchi ID: {target}")
    try:
        await bot.send_message(target, notify)
    except Exception:
        await callback.message.answer("⚠️ Foydalanuvchiga xabar yetmadi (u botni bloklagan bo'lishi mumkin).")
    await callback.answer()


# ============ 📔 VIP TAGINI TAHRIRLASH ============
@router.callback_query(F.data.startswith("vt:"))
async def cb_vip_tag(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    what = callback.data.split(":", 1)[1]
    if what == "reset":
        set_setting("vip_caption", "")
        set_setting("vip_card_text", "")
        await callback.message.answer("♻️ VIP matnlari asl holiga qaytarildi.")
        await callback.answer()
        return
    if what == "cap":
        await state.set_state(AdminFSM.waiting_vipcaption)
        current = get_vip_text("vip_caption", DEFAULT_VIP_CAPTION)
        title = "📝 Hozirgi VIP bo'lim matni:"
    elif what == "card":
        await state.set_state(AdminFSM.waiting_vipcard)
        current = get_vip_text("vip_card_text", DEFAULT_CARD_TEXT)
        title = "💳 Hozirgi karta ma'lumotlari (tarif tanlanganda chiqadi):"
    else:
        await callback.answer()
        return
    await callback.message.answer(title)
    try:
        await callback.message.answer(quote(current), parse_mode="HTML")
    except TelegramBadRequest:
        await callback.message.answer(current)
    await callback.message.answer(
        "✏️ Yangi matnni yuboring (qalin, kursiv, monospace kabi Telegram formatlari saqlanadi).\n"
        "Bekor qilish: /start"
    )
    await callback.answer()


async def _save_vip_text(message: Message, state: FSMContext, key: str, name: str):
    if not message.text:
        await message.answer("❗️ Faqat matn yuboring.")
        return
    html_text = message.html_text
    if len(html_text) > 3000:
        await message.answer(f"❗️ Matn juda uzun ({len(html_text)} belgi). 3000 belgidan oshmasin.")
        return
    set_setting(key, html_text)
    await state.clear()
    await message.answer(f"✅ {name} yangilandi. Foydalanuvchiga shunday ko'rinadi:")
    if key == "vip_caption":
        text, kb = vip_view()
    else:
        text, kb = plan_view(VIP_PLANS[0][0])
    try:
        await message.answer(text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest as e:
        logging.error(e)
        await message.answer("⚠️ Matnni ko'rsatib bo'lmadi. Formatni tekshirib, qaytadan yuboring yoki ♻️ Asl holiga qaytaring.")


@router.message(StateFilter(AdminFSM.waiting_vipcaption))
async def process_vipcaption(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await _save_vip_text(message, state, "vip_caption", "VIP bo'lim matni")


@router.message(StateFilter(AdminFSM.waiting_vipcard))
async def process_vipcard(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await _save_vip_text(message, state, "vip_card_text", "Karta ma'lumotlari")


# ============ ✈️ XABAR (barcha foydalanuvchilarga) ============
@router.message(StateFilter(AdminFSM.waiting_broadcast))
async def process_broadcast_msg(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.update_data(bc_chat=message.chat.id, bc_msg=message.message_id)
    total = db("SELECT COUNT(*) FROM users", (), "one")[0]
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        ibtn("📨 Botga xabar berish", "bc:yes", style="success"),
        ibtn("❌ Yo'q", "bc:no", style="danger"),
    ]])
    await message.reply(f"✈️ Yuqoridagi xabar botdagi {total} ta foydalanuvchiga yuborilsinmi?", reply_markup=kb)


@router.callback_query(F.data == "bc:no")
async def cb_broadcast_no(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer("👌 Xabar yuborilmadi.")
    await callback.answer()


@router.callback_query(F.data == "bc:yes")
async def cb_broadcast_yes(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    data = await state.get_data()
    chat_id, msg_id = data.get("bc_chat"), data.get("bc_msg")
    await callback.answer()
    if not chat_id or not msg_id:
        await callback.message.answer("⚠️ Sessiya tugagan, \"✈️ Xabar\" ni qaytadan bosing.")
        return
    await state.clear()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    ids = [r[0] for r in db("SELECT user_id FROM users", (), "all")]
    status = await callback.message.answer(f"✈️ Yuborilmoqda... 0/{len(ids)}")
    sent, failed = 0, 0
    for i, uid in enumerate(ids, start=1):
        try:
            await bot.copy_message(uid, chat_id, msg_id)
            sent += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
            try:
                await bot.copy_message(uid, chat_id, msg_id)
                sent += 1
            except Exception:
                failed += 1
        except Exception:
            failed += 1
        if i % 25 == 0:
            try:
                await status.edit_text(f"✈️ Yuborilmoqda... {i}/{len(ids)}")
            except Exception:
                pass
        await asyncio.sleep(0.05)
    await status.edit_text(f"✅ Yakunlandi.\n📨 Yuborildi: {sent}\n❌ Yuborilmadi: {failed}")


# ============ TO'G'RIDAN-TO'G'RI KOD/NOM YOZILSA ============
def _all_admin_labels():
    out = set()
    for menu in (admin_menu(), admin_users_menu(), admin_anime_menu(), admin_params_menu()):
        for row in menu.keyboard:
            for b in row:
                out.add(b.text)
    return out


ADMIN_BUTTON_TEXTS = _all_admin_labels()


@router.message(F.text & ~F.text.startswith("/"))
async def fallback_text(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        return
    if message.text in ADMIN_BUTTON_TEXTS:
        return
    ensure_user(message.from_user.id, message.from_user.username)
    if needs_force_sub(message.from_user.id):
        not_subscribed = await check_force_sub(message.from_user.id)
        if not_subscribed:
            await message.answer("📢 Avval kanal(lar)ga obuna bo'ling:", reply_markup=force_sub_keyboard(not_subscribed))
            return
    await _do_search(message, message.text.strip())


# ============ KEEP-ALIVE WEB SERVER ============
async def handle_ping(request):
    return web.Response(text="Anime bot ishlayapti ✅")


async def start_webserver():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"Keep-alive server {PORT}-portda ishga tushdi")


# ============ ISHGA TUSHIRISH ============
async def main():
    global BOT_USERNAME
    db_init()
    asyncio.create_task(backfill_posters())
    try:
        me = await bot.get_me()
        if me.username:
            BOT_USERNAME = me.username
    except Exception as e:
        logging.error(f"get_me xatosi: {e}")
    # Menyuda faqat /start qoladi
    await bot.set_my_commands([
        BotCommand(command="start", description="Botni ishga tushirish"),
    ])
    await start_webserver()
    print("Anime bot ishga tushdi...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
