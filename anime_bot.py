import asyncio
import logging
import os
import sqlite3
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, BotCommand
)
from aiogram.exceptions import TelegramBadRequest
from aiohttp import web

# ============ SOZLAMALAR ============
BOT_TOKEN = os.environ.get("BOT_TOKEN", "BOT_TOKEN_BU_YERGA")
INITIAL_ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "8470314807").split(",")]
DB_PATH = "anime_bot.db"

PAYMENT_CARD = "7777 0105 7309 7248"
PAYMENT_CARD_OWNER = "Paynet virtual karta"
ADMIN_USERNAME = "@Anituz_org"
VIP_PLANS = [
    ("1 oylik VIP", "15 000 so'm"),
    ("2 oylik VIP", "29 000 so'm"),
    ("3 oylik VIP", "39 000 so'm"),
    ("6 oylik VIP", "100 000 so'm"),
    ("8 oylik VIP", "140 000 so'm"),
]
MONTH_TO_DAYS = {"1oy": 30, "2oy": 60, "3oy": 90, "6oy": 180, "8oy": 240}

ANNOUNCE_CHANNEL_ID = os.environ.get("ANNOUNCE_CHANNEL_ID", "-1004371894042")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "An1Zen_bot")
ANIME_CHANNEL_LINK = os.environ.get("ANIME_CHANNEL_LINK", "https://t.me/An1Zen_an1me")
PORT = int(os.environ.get("PORT", 10000))
ONLINE_WINDOW_MIN = 5  # "online" deb hisoblanadigan faollik oynasi (daqiqa)

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
    waiting_delete_target = State()
    waiting_code_change = State()
    waiting_channel = State()
    waiting_channel_remove = State()
    waiting_episode_post = State()


# ============ DATABASE ============
def db_init():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS animes (
        code TEXT PRIMARY KEY, title TEXT, total_seasons INTEGER DEFAULT 1,
        quality TEXT, genre TEXT, rating TEXT,
        views INTEGER DEFAULT 0, downloads INTEGER DEFAULT 0, is_premium INTEGER DEFAULT 0,
        poster_file_id TEXT, added_date TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS episodes (
        anime_code TEXT, season_number INTEGER, episode_number INTEGER, file_id TEXT,
        PRIMARY KEY (anime_code, season_number, episode_number)
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, username TEXT, joined_date TEXT,
        is_vip INTEGER DEFAULT 0, vip_until TEXT, last_seen TEXT
    )""")
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
    cur.execute("""CREATE TABLE IF NOT EXISTS required_channels (
        channel_id INTEGER PRIMARY KEY, title TEXT, link TEXT, added_date TEXT
    )""")
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


def is_admin(user_id: int) -> bool:
    return bool(db("SELECT 1 FROM admins WHERE user_id=?", (user_id,), "one"))


def ensure_user(user_id: int, username: str):
    now = datetime.now().isoformat()
    if not db("SELECT 1 FROM users WHERE user_id=?", (user_id,), "one"):
        db("INSERT INTO users (user_id, username, joined_date, last_seen) VALUES (?,?,?,?)",
           (user_id, username or "", now, now))
    else:
        db("UPDATE users SET last_seen=? WHERE user_id=?", (now, user_id))


def is_vip(user_id: int) -> bool:
    row = db("SELECT is_vip, vip_until FROM users WHERE user_id=?", (user_id,), "one")
    if not row or not row[0]:
        return False
    if row[1] and row[1] != "forever":
        if datetime.fromisoformat(row[1]) < datetime.now():
            return False
    return True


def get_setting(key: str, default: str = "") -> str:
    row = db("SELECT value FROM settings WHERE key=?", (key,), "one")
    return row[0] if row else default


def set_setting(key: str, value: str):
    db("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))


# ============ KLAVIATURALAR ============
def admin_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👑 Admin huquqlari")],
            [KeyboardButton(text="👥 Foydalanuvchilar")],
            [KeyboardButton(text="🎬 Anime qo'shish"), KeyboardButton(text="🗑 Anime olish")],
            [KeyboardButton(text="➕ Qism qo'shish"), KeyboardButton(text="🗑 Qism olish")],
            [KeyboardButton(text="🔄 Kodni o'zgartirish"), KeyboardButton(text="📋 Kodlar ro'yxati")],
            [KeyboardButton(text="📢 Qism post qilish")],
            [KeyboardButton(text="🔔 Majburiy obuna")],
        ],
        resize_keyboard=True,
    )


def dashboard_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔢 Kod orqali qidirish", callback_data="search_mode:code")],
        [InlineKeyboardButton(text="🖼 Rasm orqali qidirish", callback_data="search_mode:image")],
        [InlineKeyboardButton(text="📝 Nomi orqali qidirish", callback_data="search_mode:name")],
    ])


def seasons_keyboard(code: str, total_seasons: int) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text=f"🎞 {s}-fasl", callback_data=f"season:{code}:{s}")]
               for s in range(1, total_seasons + 1)]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def episodes_keyboard(code: str, season: int, total_ep: int) -> InlineKeyboardMarkup:
    buttons, row = [], []
    for i in range(1, total_ep + 1):
        row.append(InlineKeyboardButton(text=f"▶️{i}", callback_data=f"ep:{code}:{season}:{i}"))
        if len(row) == 4:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def episode_count_kb(code: str, season: int) -> InlineKeyboardMarkup:
    total_ep = db("SELECT COUNT(*) FROM episodes WHERE anime_code=? AND season_number=?",
                  (code, season), "one")[0]
    return episodes_keyboard(code, season, total_ep)


# ============ STATISTIKA HISOBLASH ============
def compute_dashboard_stats():
    total_animes = db("SELECT COUNT(*) FROM animes", (), "one")[0]
    total_episodes = db("SELECT COUNT(*) FROM episodes", (), "one")[0]
    total_users = db("SELECT COUNT(*) FROM users", (), "one")[0]
    cutoff = (datetime.now() - timedelta(minutes=ONLINE_WINDOW_MIN)).isoformat()
    online = db("SELECT COUNT(*) FROM users WHERE last_seen >= ?", (cutoff,), "one")[0]
    return total_animes, total_episodes, total_users, online


async def send_dashboard(message: Message):
    await message.answer("🎬 Xush kelibsiz!\n\nAnime kodini yuboring.")


# ============ ANIME KARTOCHKASI ============
async def show_anime(message: Message, code: str, user_id: int):
    anime = db("SELECT * FROM animes WHERE code=?", (code,), "one")
    if not anime:
        await message.answer("❌ Bunday kodli/nomli anime topilmadi.")
        return

    (acode, title, total_seasons, quality, genre, rating, views, downloads,
     is_premium, poster_file_id, added_date) = anime

    db("UPDATE animes SET views = views + 1 WHERE code=?", (acode,))

    caption = (
        f"🎬 <b>{title}</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"🎞 Fasllar soni: {total_seasons}\n"
        f"💿 Sifati: {quality}\n"
        f"⭐️ Reyting: {rating or '—'}\n"
        f"🏷 Janri: {genre}\n"
    )
    caption += (
        "━━━━━━━━━━━━━━━\n"
        f"👀 Ko'rilgan: {views + 1} marta\n"
        f"⬇️ Yuklab olingan: {downloads} marta"
    )

    kb = seasons_keyboard(acode, total_seasons) if total_seasons > 1 else episode_count_kb(acode, 1)

    try:
        if poster_file_id:
            await message.answer_photo(poster_file_id, caption=caption, parse_mode="HTML", reply_markup=kb)
        else:
            await message.answer(caption, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest as e:
        logging.error(e)
        await message.answer(caption, parse_mode="HTML", reply_markup=kb)


# ============ /START ============
@router.message(Command("start"))
async def cmd_start(message: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    ensure_user(message.from_user.id, message.from_user.username)
    if command.args:
        arg = command.args.strip()
        if arg.startswith("ep_"):
            try:
                _,code,season,num=arg.split("_"); season=int(season); num=int(num)
                if not await check_required_channels(message): return
                ep=db("SELECT file_id FROM episodes WHERE anime_code=? AND season_number=? AND episode_number=?",(code,season,num),"one")
                if ep: await bot.send_video(message.chat.id,ep[0])
                else: await message.answer("❌ Bu qism topilmadi.")
                return
            except ValueError: pass
        code = arg
        if db("SELECT 1 FROM animes WHERE code=?", (code,), "one"):
            if not await check_required_channels(message): return
            await show_anime(message, code, message.from_user.id); return
    if is_admin(message.from_user.id):
        await message.answer("🛠 Xush kelibsiz, admin!", reply_markup=admin_menu())
    else:
        await message.answer("🎬 Xush kelibsiz!", reply_markup=ReplyKeyboardRemove())


# ============ MAJBURIY OBUNA ============
def required_channels():
    return db("SELECT channel_id, title, link FROM required_channels ORDER BY added_date", (), "all")

async def check_required_channels(message: Message) -> bool:
    channels = required_channels()
    if not channels:
        return True
    missing = []
    for cid, title, link in channels:
        try:
            member = await bot.get_chat_member(cid, message.from_user.id)
            if member.status in ("left", "kicked"):
                missing.append((title or str(cid), link or ANIME_CHANNEL_LINK))
        except Exception:
            missing.append((title or str(cid), link or ANIME_CHANNEL_LINK))
    if not missing:
        return True
    rows = [[InlineKeyboardButton(text="✅ Kanal obuna ✅", url=link)] for _, link in missing]
    rows.append([InlineKeyboardButton(text="🔎 Tekshirish", callback_data="sub:check")])
    await message.answer("⚠️ Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    return False

@router.callback_query(F.data == "sub:check")
async def cb_sub_check(callback: CallbackQuery):
    class Dummy: pass
    ok = True
    channels = required_channels()
    missing=[]
    for cid,title,link in channels:
        try:
            m=await bot.get_chat_member(cid, callback.from_user.id)
            if m.status in ("left","kicked"): missing.append((title or str(cid),link or ANIME_CHANNEL_LINK))
        except Exception: missing.append((title or str(cid),link or ANIME_CHANNEL_LINK))
    if missing:
        rows=[[InlineKeyboardButton(text="✅ Kanal obuna ✅", url=link)] for _,link in missing]
        rows.append([InlineKeyboardButton(text="🔎 Tekshirish", callback_data="sub:check")])
        await callback.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        await callback.answer("❌ Hali barcha kanallarga obuna bo'lmagansiz.", show_alert=True)
    else:
        await callback.message.edit_text("✅ Obuna tasdiqlandi. Endi anime kodini yuborishingiz mumkin.")
        await callback.answer("✅ Hammasi joyida!")

# ============ QIDIRISH ============
@router.callback_query(F.data.startswith("search_mode:"))
async def cb_search_mode(callback: CallbackQuery, state: FSMContext):
    mode = callback.data.split(":")[1]
    if mode == "image":
        await callback.message.answer(
            "🖼 Hozircha rasm orqali avtomatik tanish qo'llab-quvvatlanmaydi.\n"
            "Iltimos, anime nomini yozib yuboring — shunga yaqin nomlarni topib beraman:"
        )
    elif mode == "code":
        await callback.message.answer("🔢 Anime kodini yuboring:")
    else:
        await callback.message.answer("📝 Anime nomini yuboring:")
    await state.set_state(SearchStates.waiting_query)
    await callback.answer()


@router.message(StateFilter(SearchStates.waiting_query))
async def process_search(message: Message, state: FSMContext):
    await state.clear()
    query = message.text.strip()
    ensure_user(message.from_user.id, message.from_user.username)
    await _do_search(message, query)


async def _do_search(message: Message, query: str):
    if not await check_required_channels(message):
        return
    exact = db("SELECT code FROM animes WHERE code=?", (query,), "one")
    if exact:
        await show_anime(message, exact[0], message.from_user.id)
        return
    matches = db("SELECT code, title FROM animes WHERE title LIKE ?", (f"%{query}%",), "all")
    if not matches:
        await message.answer("❌ Hech narsa topilmadi.")
        return
    if len(matches) == 1:
        await show_anime(message, matches[0][0], message.from_user.id)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=f"open:{c}")] for c, t in matches[:15]
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
    kb = episode_count_kb(code, int(season))
    await callback.message.answer(f"🎞 {season}-fasl qismlari:", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("ep:"))
async def cb_episode(callback: CallbackQuery):
    _, code, season, num = callback.data.split(":")
    season, num = int(season), int(num)
    ensure_user(callback.from_user.id, callback.from_user.username)
    ep = db("SELECT file_id FROM episodes WHERE anime_code=? AND season_number=? AND episode_number=?",
            (code, season, num), "one")
    if not ep:
        await callback.answer("❌ Bu qism hali yuklanmagan.", show_alert=True)
        return
    try:
        await bot.send_video(callback.message.chat.id, ep[0])
        db("UPDATE animes SET downloads = downloads + 1 WHERE code=?", (code,))
        db("INSERT OR REPLACE INTO history (user_id, anime_code, season_number, episode_number, watched_date) VALUES (?,?,?,?,?)",
           (callback.from_user.id, code, season, num, datetime.now().isoformat()))
    except TelegramBadRequest as e:
        logging.error(e)
        await callback.answer("⚠️ Xatolik yuz berdi.", show_alert=True)
        return
    await callback.answer()


# ============ ADMIN: DASHBOARD TUGMALARI ============
@router.message(F.text == "👑 Admin huquqlari")
async def btn_admin_manage(message: Message):
    if not is_admin(message.from_user.id): return
    kb=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Admin qo'shish", callback_data="adm:add")],
        [InlineKeyboardButton(text="➖ Admin olish", callback_data="adm:list")],
        [InlineKeyboardButton(text="📋 Adminlar ro'yxati", callback_data="adm:list")],
    ])
    await message.answer("👑 Admin huquqlari", reply_markup=kb)

@router.callback_query(F.data == "adm:add")
async def cb_admin_add(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id): return
    await callback.message.answer("Admin qilinadigan foydalanuvchining Telegram ID sini yuboring:")
    await state.set_state(AdminFSM.waiting_new_admin_id); await callback.answer()

@router.message(StateFilter(AdminFSM.waiting_new_admin_id))
async def process_new_admin(message: Message, state: FSMContext):
    await state.clear()
    try: new_id=int(message.text.strip())
    except ValueError:
        await message.answer("❗️ Faqat ID raqam yuboring."); return
    display=str(new_id)
    try:
        chat=await bot.get_chat(new_id); display=("@"+chat.username) if chat.username else (chat.full_name or display)
    except Exception: pass
    db("INSERT OR REPLACE INTO admins (user_id, username, added_date) VALUES (?,?,?)",(new_id,display,datetime.now().isoformat()))
    await message.answer(f"✅ Admin qo'shildi: {display} ({new_id})")

@router.callback_query(F.data == "adm:list")
async def cb_admin_list(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    admins=db("SELECT user_id,username FROM admins ORDER BY added_date",(),"all")
    rows=[]
    for aid,uname in admins:
        rows.append([InlineKeyboardButton(text=f"❌ {uname or aid}", callback_data=f"adm:rm:{aid}")])
    await callback.message.answer("📋 Adminlar ro'yxati. Tugmani bossangiz admin olinadi:",reply_markup=InlineKeyboardMarkup(inline_keyboard=rows or [[InlineKeyboardButton(text="—",callback_data="adm:none")]])); await callback.answer()

@router.callback_query(F.data.startswith("adm:rm:"))
async def cb_admin_remove(callback: CallbackQuery):
    target=int(callback.data.split(":")[2])
    if target in INITIAL_ADMIN_IDS:
        await callback.answer("❌ Asosiy adminni olib bo'lmaydi.",show_alert=True); return
    db("DELETE FROM admins WHERE user_id=?",(target,)); await callback.message.answer(f"🗑 {target} adminlikdan olindi."); await callback.answer()

# ============ ADMIN: FOYDALANUVCHILAR / STATISTIKA ============
@router.message(F.text == "👥 Foydalanuvchilar")
async def btn_users(message: Message):
    if not is_admin(message.from_user.id): return
    total=db("SELECT COUNT(*) FROM users",(),"one")[0]
    cutoff=(datetime.now()-timedelta(minutes=ONLINE_WINDOW_MIN)).isoformat()
    online=db("SELECT COUNT(*) FROM users WHERE last_seen>=?",(cutoff,),"one")[0]
    admins=db("SELECT COUNT(*) FROM admins",(),"one")[0]
    online_admins=db("SELECT COUNT(*) FROM users WHERE user_id IN (SELECT user_id FROM admins) AND last_seen>=?",(cutoff,),"one")[0]
    await message.answer(f"👥 <b>Foydalanuvchilar statistikasi</b>\n\n👤 Jami: <b>{total}</b>\n🟢 Online: <b>{online}</b>\n👑 Adminlar: <b>{admins}</b>\n🟢 Online adminlar: <b>{online_admins}</b>",parse_mode="HTML")

# ============ ADMIN: ANIME QO'SHISH ============
@router.message(F.text == "🎬 Anime qo'shish")
async def btn_add_anime(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "🖼 Anime uchun rasm (poster) yuboring, rasm ostiga (caption) shu shaklda yozing:\n\n"
        "<code>Kod: 25\n"
        "Nomi: Anime nomi\n"
        "Fasllar: 3\n"
        "Sifat: 720p\n"
        "Reyting: 8.5\n"
        "Janr: Isekai, jangari\n"
        "Premium: yo'q</code>\n\n"
        "⚠️ \"Kod\" qatori — bu ichki, texnik kod, u foydalanuvchilarga hech qachon ko'rsatilmaydi. "
        "Har bir kod faqat bitta animega tegishli bo'lishi kerak.",
        parse_mode="HTML",
    )


@router.message(F.photo, F.caption.contains("Kod:"))
async def process_addanime(message: Message):
    if not is_admin(message.from_user.id):
        return
    fields = {}
    for line in message.caption.split("\n"):
        if ":" in line:
            key, val = line.split(":", 1)
            fields[key.strip().lower()] = val.strip()

    try:
        code = fields["kod"]
        title = fields["nomi"]
        total_seasons = int(fields.get("fasllar", "1"))
        quality = fields.get("sifat", "-")
        rating = fields.get("reyting", "-")
        genre = fields.get("janr", "-")
        is_premium = 0
    except (KeyError, ValueError) as e:
        await message.answer(f"❌ Ma'lumot to'liq emas: {e}")
        return

    existing = db("SELECT title FROM animes WHERE code=?", (code,), "one")
    if existing and existing[0] != title:
        await message.answer(
            f"⚠️ Bu kod (\"{code}\") allaqachon \"{existing[0]}\" animesi uchun band. "
            "Boshqa kod tanlang."
        )
        return

    poster_file_id = message.photo[-1].file_id
    db("""INSERT OR REPLACE INTO animes
          (code, title, total_seasons, quality, genre, rating,
           views, downloads, is_premium, poster_file_id, added_date)
          VALUES (?,?,?,?,?,?,COALESCE((SELECT views FROM animes WHERE code=?),0),
                  COALESCE((SELECT downloads FROM animes WHERE code=?),0),?,?,?)""",
       (code, title, total_seasons, quality, genre, rating, code, code, is_premium, poster_file_id,
        datetime.now().isoformat()))

    await message.answer(
        f"✅ Anime qo'shildi!\nKod: <code>{code}</code>\n\n"
        f"Endi \"➕ Qism qo'shish\" tugmasi orqali qismlarini yuklang.",
        parse_mode="HTML",
    )

    if ANNOUNCE_CHANNEL_ID and BOT_USERNAME:
        announce_caption = (
            f"🎬 {title}\n"
            f"🎞 Fasllar: {total_seasons}\n"
            f"💿 Sifat: {quality} | 🏷 Janr: {genre}\n"
            f"⭐️ Reyting: {rating}"
        )
        watch_kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="💈Anime Korish💈", url=f"https://t.me/{BOT_USERNAME}?start={code}")
        ]])
        try:
            await bot.send_photo(int(ANNOUNCE_CHANNEL_ID), poster_file_id,
                                  caption=announce_caption, reply_markup=watch_kb)
        except TelegramBadRequest as e:
            logging.error(f"Announce error: {e}")
            await message.answer("⚠️ Anime qo'shildi, lekin kanalga e'lon qilishda xatolik (bot kanalga admin ekanini tekshiring).")


# ============ ADMIN: QISM QO'SHISH ============
@router.message(F.text == "➕ Qism qo'shish")
async def btn_add_episode(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "➕ Qism qo'shishning 2 usuli bor:\n\n"
        "1) Videoni to'g'ridan-to'g'ri botga yuboring, caption qismiga yozing:\n"
        "<code>/addep KOD FASL QISM</code>\n\n"
        "2) Videoni (boshqa botdan/kanaldan) botga <b>forward</b> qiling, "
        "so'ng o'sha forward qilingan xabarga <b>javob (reply)</b> tariqasida yozing:\n"
        "<code>/addep KOD FASL QISM</code>",
        parse_mode="HTML",
    )


@router.message(F.video, F.caption, F.caption.startswith("/addep"))
async def process_addep_direct(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.caption.split()
    if len(parts) != 4:
        await message.answer("❗️ Format: /addep KOD FASL QISM  (masalan: /addep 25 1 1)")
        return
    _, code, season, num = parts
    try:
        season, num = int(season), int(num)
    except ValueError:
        await message.answer("❗️ Fasl va qism raqam bo'lishi kerak.")
        return
    if not db("SELECT 1 FROM animes WHERE code=?", (code,), "one"):
        await message.answer("❌ Bunday kodli anime topilmadi, avval anime qo'shing.")
        return
    file_id = message.video.file_id
    db("INSERT OR REPLACE INTO episodes (anime_code, season_number, episode_number, file_id) VALUES (?,?,?,?)",
       (code, season, num, file_id))
    await message.answer(f"✅ {code}-anime, {season}-fasl, {num}-qism qo'shildi!")


@router.message(Command("addep"))
async def cmd_addep_reply(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    if message.reply_to_message and message.reply_to_message.video and command.args:
        parts = command.args.split()
        if len(parts) != 3:
            await message.answer("❗️ Format: /addep KOD FASL QISM")
            return
        code, season, num = parts
        try:
            season, num = int(season), int(num)
        except ValueError:
            await message.answer("❗️ Fasl va qism raqam bo'lishi kerak.")
            return
        if not db("SELECT 1 FROM animes WHERE code=?", (code,), "one"):
            await message.answer("❌ Bunday kodli anime topilmadi.")
            return
        file_id = message.reply_to_message.video.file_id
        db("INSERT OR REPLACE INTO episodes (anime_code, season_number, episode_number, file_id) VALUES (?,?,?,?)",
           (code, season, num, file_id))
        await message.answer(f"✅ {code}-anime, {season}-fasl, {num}-qism qo'shildi!")
        return
    await message.answer(
        "❗️ Videoni caption bilan yuboring (/addep KOD FASL QISM), "
        "yoki forward qilingan videoga javoban shu buyruqni yozing."
    )


# ============ ADMIN: ANIME / QISM O'CHIRISH ============
@router.message(F.text == "🗑 Anime/qism o'chirish")
async def btn_delete(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "🗑 Butun animeni o'chirish uchun — anime kodi yoki nomini yuboring.\n"
        "Faqat bitta qismni o'chirish uchun — <code>KOD FASL QISM</code> shaklida yuboring.\n"
        "Masalan: <code>25</code> (butun anime) yoki <code>25 1 3</code> (faqat 1-fasl 3-qism)",
        parse_mode="HTML",
    )
    await state.set_state(AdminFSM.waiting_delete_target)


@router.message(StateFilter(AdminFSM.waiting_delete_target))
async def process_delete(message: Message, state: FSMContext):
    await state.clear()
    parts = message.text.strip().split()

    if len(parts) == 3:
        code, season, num = parts
        try:
            season, num = int(season), int(num)
        except ValueError:
            await message.answer("❗️ Fasl va qism raqam bo'lishi kerak.")
            return
        db("DELETE FROM episodes WHERE anime_code=? AND season_number=? AND episode_number=?",
           (code, season, num))
        await message.answer(f"🗑 {code}-anime, {season}-fasl, {num}-qism o'chirildi.")
        return

    query = message.text.strip()
    exact = db("SELECT code, title FROM animes WHERE code=?", (query,), "one")
    if exact:
        code = exact[0]
        db("DELETE FROM animes WHERE code=?", (code,))
        db("DELETE FROM episodes WHERE anime_code=?", (code,))
        db("DELETE FROM history WHERE anime_code=?", (code,))
        await message.answer(f"🗑 \"{exact[1]}\" (kod: {code}) butunlay o'chirildi.")
        return

    matches = db("SELECT code, title FROM animes WHERE title LIKE ?", (f"%{query}%",), "all")
    if not matches:
        await message.answer("❌ Bunday anime topilmadi.")
        return
    if len(matches) == 1:
        code, title = matches[0]
        db("DELETE FROM animes WHERE code=?", (code,))
        db("DELETE FROM episodes WHERE anime_code=?", (code,))
        db("DELETE FROM history WHERE anime_code=?", (code,))
        await message.answer(f"🗑 \"{title}\" (kod: {code}) butunlay o'chirildi.")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=f"delconfirm:{c}")] for c, t in matches[:15]
    ])
    await message.answer("Bir nechta natija topildi, o'chirmoqchi bo'lganingizni tanlang:", reply_markup=kb)


@router.callback_query(F.data.startswith("delconfirm:"))
async def cb_delconfirm(callback: CallbackQuery):
    code = callback.data.split(":")[1]
    title_row = db("SELECT title FROM animes WHERE code=?", (code,), "one")
    db("DELETE FROM animes WHERE code=?", (code,))
    db("DELETE FROM episodes WHERE anime_code=?", (code,))
    db("DELETE FROM history WHERE anime_code=?", (code,))
    await callback.message.answer(f"🗑 \"{title_row[0] if title_row else code}\" o'chirildi.")
    await callback.answer()


# ============ TO'G'RIDAN-TO'G'RI KOD YOZILSA (menyudan tashqari) ============
@router.message(F.text & ~F.text.startswith("/"))
async def fallback_text(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        return  # boshqa handler kutmoqda
    ensure_user(message.from_user.id, message.from_user.username)
    admin_buttons = {
        "👑 Admin huquqlari", "👥 Foydalanuvchilar", "🎬 Anime qo'shish", "🗑 Anime olish",
        "➕ Qism qo'shish", "🗑 Qism olish", "🔄 Kodni o'zgartirish", "📋 Kodlar ro'yxati",
        "📢 Qism post qilish", "🔔 Majburiy obuna",
    }
    if message.text in admin_buttons:
        return
    await _do_search(message, message.text.strip())


# ============ ADMIN: ANIME OLIB TASHLASH ============
@router.message(F.text == "🗑 Anime olish")
async def btn_delete_anime(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await message.answer("Anime kodini yuboring:")
    await state.set_state(AdminFSM.waiting_delete_target)

# ============ ADMIN: QISM OLIB TASHLASH ============
@router.message(F.text == "🗑 Qism olish")
async def btn_delete_episode(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await message.answer("Format: KOD FASL QISM\nMasalan: 25 1 14")
    await state.set_state(AdminFSM.waiting_delete_target)

# ============ ADMIN: KODNI O'ZGARTIRISH ============
@router.message(F.text == "🔄 Kodni o'zgartirish")
async def btn_code_change(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await message.answer("Format: ESKI_KOD YANGI_KOD")
    await state.set_state(AdminFSM.waiting_code_change)

@router.message(StateFilter(AdminFSM.waiting_code_change))
async def process_code_change(message: Message, state: FSMContext):
    await state.clear(); parts=message.text.split()
    if len(parts)!=2: await message.answer("❗️ Masalan: 25 125"); return
    old,new=parts
    if not db("SELECT 1 FROM animes WHERE code=?",(old,),"one"): await message.answer("❌ Eski kod topilmadi."); return
    if db("SELECT 1 FROM animes WHERE code=?",(new,),"one"): await message.answer("❌ Yangi kod band."); return
    db("UPDATE animes SET code=? WHERE code=?",(new,old)); db("UPDATE episodes SET anime_code=? WHERE anime_code=?",(new,old)); db("UPDATE history SET anime_code=? WHERE anime_code=?",(new,old))
    await message.answer(f"✅ Kod o'zgartirildi: {old} → {new}")

@router.message(F.text == "📋 Kodlar ro'yxati")
async def btn_code_list(message: Message):
    if not is_admin(message.from_user.id): return
    rows=db("SELECT code,title FROM animes ORDER BY added_date DESC",(),"all")
    if not rows: await message.answer("📋 Kodlar ro'yxati bo'sh."); return
    await message.answer("📋 Kodlar ro'yxati:\n\n"+"\n".join(f"<code>{c}</code> — {t}" for c,t in rows),parse_mode="HTML")

# ============ ADMIN: QISM POST QILISH ============
@router.message(F.text == "📢 Qism post qilish")
async def btn_episode_post(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await message.answer("Kanalga chiqariladigan qism videosini botga forward qiling. Keyin o'sha video xabariga reply qilib /postep KOD FASL QISM yozing.")
    await state.set_state(AdminFSM.waiting_episode_post)

@router.message(Command("postep"))
async def cmd_postep(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id): return
    if not message.reply_to_message or not message.reply_to_message.video or not command.args:
        await message.answer("Format: forward qilingan videoga reply → /postep KOD FASL QISM"); return
    parts=command.args.split()
    if len(parts)!=3: await message.answer("❗️ Format: /postep KOD FASL QISM"); return
    code,season,num=parts
    try: season=int(season); num=int(num)
    except: await message.answer("❗️ Fasl va qism raqam bo'lishi kerak."); return
    anime=db("SELECT title FROM animes WHERE code=?",(code,),"one")
    if not anime: await message.answer("❌ Anime topilmadi."); return
    ep=db("SELECT file_id FROM episodes WHERE anime_code=? AND season_number=? AND episode_number=?",(code,season,num),"one")
    if not ep:
        file_id=message.reply_to_message.video.file_id
        db("INSERT OR REPLACE INTO episodes (anime_code,season_number,episode_number,file_id) VALUES (?,?,?,?)",(code,season,num,file_id))
    else: file_id=ep[0]
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"🎰 {num} - qism ko'rish 🎰",url=f"https://t.me/{BOT_USERNAME}?start=ep_{code}_{season}_{num}")]])
    try:
        await bot.send_video(int(ANNOUNCE_CHANNEL_ID),file_id,caption=f"🎬 {anime[0]}\n🎞 {season}-fasl {num}-qism",reply_markup=kb)
        await message.answer("✅ Qism kanalga post qilindi.")
    except Exception as e:
        logging.exception(e); await message.answer("❌ Kanalga post qilishda xatolik. Bot kanalga admin ekanini tekshiring.")

# ============ ADMIN: MAJBURIY OBUNA ============
@router.message(F.text == "🔔 Majburiy obuna")
async def btn_required(message: Message):
    if not is_admin(message.from_user.id): return
    rows=required_channels()
    kb=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Kanal qo'shish",callback_data="subadm:add")],
        [InlineKeyboardButton(text="➖ Kanal olish",callback_data="subadm:remove")],
        [InlineKeyboardButton(text="📋 Kanallar ro'yxati",callback_data="subadm:list")],
    ])
    await message.answer(f"🔔 Majburiy obuna kanallari: <b>{len(rows)}</b>",parse_mode="HTML",reply_markup=kb)

@router.callback_query(F.data == "subadm:add")
async def subadm_add(callback: CallbackQuery,state:FSMContext):
    if not is_admin(callback.from_user.id): return
    await callback.message.answer("Format: KANAL_ID | LINK\nMasalan: -1001234567890 | https://t.me/example")
    await state.set_state(AdminFSM.waiting_channel); await callback.answer()

@router.message(StateFilter(AdminFSM.waiting_channel))
async def process_channel(message: Message,state:FSMContext):
    await state.clear(); parts=[x.strip() for x in message.text.split("|",1)]
    if len(parts)!=2: await message.answer("❗️ Format noto'g'ri."); return
    try: cid=int(parts[0])
    except: await message.answer("❗️ Kanal ID raqam bo'lishi kerak."); return
    link=parts[1]
    title=str(cid)
    try: title=(await bot.get_chat(cid)).title or title
    except: pass
    db("INSERT OR REPLACE INTO required_channels(channel_id,title,link,added_date) VALUES (?,?,?,?)",(cid,title,link,datetime.now().isoformat()))
    await message.answer(f"✅ Kanal majburiy obunaga qo'shildi: {title}")

@router.callback_query(F.data == "subadm:list")
async def subadm_list(callback:CallbackQuery):
    if not is_admin(callback.from_user.id): return
    rows=required_channels()
    text="📋 Majburiy kanallar:\n\n"+"\n".join(f"{t} — <code>{cid}</code>\n{link}" for cid,t,link in rows) if rows else "📋 Ro'yxat bo'sh."
    await callback.message.answer(text,parse_mode="HTML"); await callback.answer()

@router.callback_query(F.data == "subadm:remove")
async def subadm_remove(callback:CallbackQuery,state:FSMContext):
    if not is_admin(callback.from_user.id): return
    await callback.message.answer("O'chiriladigan kanal ID sini yuboring:"); await state.set_state(AdminFSM.waiting_channel_remove); await callback.answer()

@router.message(StateFilter(AdminFSM.waiting_channel_remove))
async def process_channel_remove(message:Message,state:FSMContext):
    await state.clear()
    try: cid=int(message.text.strip())
    except: await message.answer("❗️ ID raqam bo'lishi kerak."); return
    db("DELETE FROM required_channels WHERE channel_id=?",(cid,)); await message.answer("✅ Kanal olib tashlandi.")

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
    db_init()
    await bot.set_my_commands([BotCommand(command="start", description="Botni ishga tushirish")])
    await start_webserver()
    print("Anime bot ishga tushdi...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
