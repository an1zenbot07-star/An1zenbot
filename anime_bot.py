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
PAYMENT_CARD_OWNER = "M.Abduvakhobov"
ADMIN_USERNAME = "@an1zen"
VIP_PLANS = [
    ("1oy", "1 oylik", "15 000 so'm"),
    ("3oy", "3 oylik", "39 000 so'm"),
    ("5oy", "5 oylik", "49 000 so'm"),
    ("8oy", "8 oylik", "79 000 so'm"),
]
MONTH_TO_DAYS = {"1oy": 30, "3oy": 90, "5oy": 150, "8oy": 240}

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


# ============ DATABASE ============
def db_init():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS animes (
        code TEXT PRIMARY KEY, title TEXT, total_seasons INTEGER DEFAULT 1,
        total_episodes_declared INTEGER DEFAULT 0, genre TEXT, channel_name TEXT,
        quality TEXT, rating TEXT,
        views INTEGER DEFAULT 0, downloads INTEGER DEFAULT 0, is_premium INTEGER DEFAULT 0,
        poster_file_id TEXT, added_date TEXT
    )""")
    # Eski bazalarda yangi ustunlar yo'q bo'lishi mumkin — xavfsiz migratsiya
    for col, coltype in [("channel_name", "TEXT"), ("total_episodes_declared", "INTEGER DEFAULT 0")]:
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
            db("UPDATE users SET is_vip=0, vip_until=NULL WHERE user_id=?", (user_id,))
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
            [KeyboardButton(text="1️⃣ Admin qo'shish/olish")],
            [KeyboardButton(text="2️⃣ Foydalanuvchilar")],
            [KeyboardButton(text="3️⃣ Anime qo'shish"), KeyboardButton(text="4️⃣ Anime olish")],
            [KeyboardButton(text="5️⃣ Qism qo'shish"), KeyboardButton(text="6️⃣ Qism olish")],
            [KeyboardButton(text="7️⃣ Kodni o'zgartirish"), KeyboardButton(text="8️⃣ Kodlar ro'yxati")],
            [KeyboardButton(text="9️⃣ Qism post qilish")],
            [KeyboardButton(text="📢 Kanal reklama"), KeyboardButton(text="🔒 Majburiy obuna")],
            [KeyboardButton(text="📊 Statistika")],
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


async def send_dashboard(message: Message):
    total_animes, total_episodes, total_users, online = compute_dashboard_stats()
    text = (
        "🎬 <b>Anime botiga xush kelibsiz!</b>\n\n"
        f"📚 Animelar: <b>{total_animes}</b> ta\n"
        f"🎞 Jami qismlar: <b>{total_episodes}</b> ta\n"
        f"👥 Foydalanuvchilar: <b>{total_users}</b> ta\n"
        f"🟢 Hozir online: <b>{online}</b> kishi\n\n"
        "Anime topish uchun quyidagidan birini tanlang, "
        "yoki shunchaki anime kodini yozib yuboring 👇"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=dashboard_inline())


# ============ ANIME KARTOCHKASI ============
async def show_anime(message: Message, code: str, user_id: int):
    anime = db("SELECT * FROM animes WHERE code=?", (code,), "one")
    if not anime:
        await message.answer("❌ Bunday kodli/nomli anime topilmadi.")
        return

    (acode, title, total_seasons, total_episodes_declared, genre, channel_name, quality, rating,
     views, downloads, is_premium, poster_file_id, added_date) = anime

    db("UPDATE animes SET views = views + 1 WHERE code=?", (acode,))
    display_channel = channel_name or get_setting("ad_channel", "")

    caption = (
        f"🎬 <b>{title}</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"🎞 Fasllar soni: {total_seasons}\n"
    )
    if total_episodes_declared:
        caption += f"📀 Qismlar: {total_episodes_declared}\n"
    caption += f"🏷 Janri: {genre}\n"
    if display_channel:
        caption += f"📢 Kanal: {display_channel}\n"
    caption += (
        "━━━━━━━━━━━━━━━\n"
        f"👀 Ko'rilgan: {views + 1} marta\n"
        f"⬇️ Yuklab olingan: {downloads} marta"
    )

    locked = is_premium and not is_vip(user_id)
    if locked:
        caption += "\n\n💎 <b>Bu — faqat VIP foydalanuvchilar uchun anime!</b>\n/vip buyrug'i orqali VIP bo'ling."

    kb = None
    if not locked:
        kb = seasons_keyboard(acode, total_seasons) if total_seasons > 1 else episode_count_kb(acode, 1)

    try:
        if poster_file_id:
            await message.answer_photo(poster_file_id, caption=caption, parse_mode="HTML", reply_markup=kb)
        else:
            await message.answer(caption, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest as e:
        logging.error(e)
        await message.answer(caption, parse_mode="HTML", reply_markup=kb)


async def send_episode(message: Message, user_id: int, code: str, season: int, num: int):
    anime = db("SELECT is_premium FROM animes WHERE code=?", (code,), "one")
    if not anime:
        await message.answer("❌ Bunday anime topilmadi.")
        return
    if anime[0] and not is_vip(user_id):
        await message.answer("💎 Bu qism faqat VIP uchun! /vip orqali VIP bo'ling.")
        return
    ep = db("SELECT file_id FROM episodes WHERE anime_code=? AND season_number=? AND episode_number=?",
            (code, season, num), "one")
    if not ep:
        await message.answer("❌ Bu qism hali yuklanmagan.")
        return
    await bot.send_video(message.chat.id, ep[0])
    db("UPDATE animes SET downloads = downloads + 1 WHERE code=?", (code,))
    db("INSERT OR REPLACE INTO history (user_id, anime_code, season_number, episode_number, watched_date) VALUES (?,?,?,?,?)",
       (user_id, code, season, num, datetime.now().isoformat()))


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
        await message.answer("🛠 Xush kelibsiz, admin!", reply_markup=admin_menu())
    else:
        await message.answer("🎬 Xush kelibsiz!", reply_markup=ReplyKeyboardRemove())
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


# ============ QIDIRISH ============
@router.callback_query(F.data.startswith("search_mode:"))
async def cb_search_mode(callback: CallbackQuery, state: FSMContext):
    mode = callback.data.split(":")[1]
    if mode == "image":
        await callback.message.answer(
            "🖼 Hozircha rasm orqali avtomatik tanish qo'llab-quvvatlanmaydi.\n"
            "Iltimos, anime nomini yozib yuboring:"
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
    ensure_user(message.from_user.id, message.from_user.username)
    if needs_force_sub(message.from_user.id):
        not_subscribed = await check_force_sub(message.from_user.id)
        if not_subscribed:
            await message.answer("📢 Avval kanal(lar)ga obuna bo'ling:", reply_markup=force_sub_keyboard(not_subscribed))
            return
    await _do_search(message, message.text.strip())


async def _do_search(message: Message, query: str):
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
    try:
        await send_episode(callback.message, callback.from_user.id, code, season, num)
    except Exception as e:
        logging.error(e)
        await callback.answer("⚠️ Xatolik yuz berdi.", show_alert=True)
        return
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
            if duration == "forever":
                days = None
            elif duration in MONTH_TO_DAYS:
                days = MONTH_TO_DAYS[duration]
            else:
                await message.answer("❗️ Muddat: 1oy, 3oy, 5oy, 8oy yoki forever")
                return
            ensure_user(target_id, "")
            until = "forever" if days is None else (datetime.now() + timedelta(days=days)).isoformat()
            db("UPDATE users SET is_vip=1, vip_until=? WHERE user_id=?", (until, target_id))
            await message.answer(f"✅ {target_id} foydalanuvchiga VIP berildi ({duration}).")
            try:
                await bot.send_message(target_id, "🎉 Tabriklaymiz! Sizga VIP faollashtirildi.")
            except Exception:
                pass
            return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{label} — {price}", callback_data=f"vipplan:{key}")]
        for key, label, price in VIP_PLANS
    ])
    await message.answer(
        f"💎 VIP olish uchun tarif tanlang\n📩 Murojaat uchun: {ADMIN_USERNAME}",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("vipplan:"))
async def cb_vip_plan(callback: CallbackQuery):
    await callback.message.answer(
        f"💳 Karta raqami: <code>{PAYMENT_CARD}</code>\n"
        f"👤 Karta egasi: {PAYMENT_CARD_OWNER}\n"
        f"📩 Murojaat uchun: {ADMIN_USERNAME}\n\n"
        "‼️ Diqqat: pul solganingizni isbotlash uchun chek ko'rsatishingiz kerak ‼️",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(Command("profil"))
async def cmd_profil(message: Message):
    ensure_user(message.from_user.id, message.from_user.username)
    watched = db("SELECT COUNT(DISTINCT anime_code) FROM history WHERE user_id=?",
                 (message.from_user.id,), "one")[0]
    vip_status = "❌ Yo'q"
    if is_vip(message.from_user.id):
        row = db("SELECT vip_until FROM users WHERE user_id=?", (message.from_user.id,), "one")
        vip_status = "♾ Cheksiz" if row[0] == "forever" else f"✅ {row[0][:10]} gacha"
    await message.answer(
        f"👤 <b>Profilingiz</b>\n\n"
        f"🆔 ID: <code>{message.from_user.id}</code>\n"
        f"💎 VIP holati: {vip_status}\n"
        f"🎬 Ko'rilgan animelar: {watched} ta",
        parse_mode="HTML",
    )


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


# ============ 1️⃣ ADMIN QO'SHISH/OLISH ============
@router.message(F.text == "1️⃣ Admin qo'shish/olish")
async def btn_admin_manage(message: Message):
    if not is_admin(message.from_user.id):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Admin qo'shish", callback_data="adm:add")],
        [InlineKeyboardButton(text="📋 Adminlar ro'yxati / olib tashlash", callback_data="adm:list")],
    ])
    await message.answer("👑 Admin boshqaruvi:", reply_markup=kb)


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


@router.callback_query(F.data == "adm:list")
async def cb_admin_list(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    admins = db("SELECT user_id, username FROM admins", (), "all")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"👤 {uname or aid}", callback_data=f"adm:sel:{aid}")] for (aid, uname) in admins
    ])
    await callback.message.answer("📋 Hozirgi adminlar:", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("adm:sel:"))
async def cb_admin_select(callback: CallbackQuery):
    target_id = callback.data.split(":")[2]
    row = db("SELECT username FROM admins WHERE user_id=?", (int(target_id),), "one")
    display_name = row[0] if row and row[0] else target_id
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Admin olib tashlash", callback_data=f"adm:rm:{target_id}")],
        [InlineKeyboardButton(text="✅ Yo'q, admin qolsin", callback_data="adm:keep")],
    ])
    await callback.message.answer(f"👤 {display_name}\nNima qilamiz?", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("adm:rm:"))
async def cb_admin_remove(callback: CallbackQuery):
    target_id = int(callback.data.split(":")[2])
    row = db("SELECT username FROM admins WHERE user_id=?", (target_id,), "one")
    display_name = row[0] if row and row[0] else str(target_id)
    db("DELETE FROM admins WHERE user_id=?", (target_id,))
    await callback.message.answer(f"🗑 {display_name} admin huquqidan olib tashlandi.")
    await callback.answer()


@router.callback_query(F.data == "adm:keep")
async def cb_admin_keep(callback: CallbackQuery):
    await callback.message.answer("✅ O'zgarishsiz qoldi.")
    await callback.answer()


# ============ 2️⃣ FOYDALANUVCHILAR ============
@router.message(F.text == "2️⃣ Foydalanuvchilar")
async def btn_users(message: Message):
    if not is_admin(message.from_user.id):
        return
    total = db("SELECT COUNT(*) FROM users", (), "one")[0]
    cutoff = (datetime.now() - timedelta(minutes=ONLINE_WINDOW_MIN)).isoformat()
    online = db("SELECT COUNT(*) FROM users WHERE last_seen >= ?", (cutoff,), "one")[0]
    month_cutoff = (datetime.now() - timedelta(days=MONTHLY_WINDOW_DAYS)).isoformat()
    monthly_active = db("SELECT COUNT(*) FROM users WHERE last_seen >= ?", (month_cutoff,), "one")[0]
    today = datetime.now().date().isoformat()
    new_today = db("SELECT COUNT(*) FROM users WHERE joined_date LIKE ?", (f"{today}%",), "one")[0]
    admins_total = db("SELECT COUNT(*) FROM admins", (), "one")[0]
    online_admins = db(
        "SELECT COUNT(*) FROM admins a JOIN users u ON a.user_id=u.user_id WHERE u.last_seen >= ?",
        (cutoff,), "one")[0]
    await message.answer(
        "👥 <b>Foydalanuvchilar statistikasi</b>\n\n"
        f"👤 Botda jami: {total} kishi\n"
        f"🆕 Bugun qo'shilgan: {new_today} kishi\n"
        f"📅 Oylik faol foydalanuvchilar (so'nggi {MONTHLY_WINDOW_DAYS} kun): {monthly_active} kishi\n"
        f"🟢 Hozir online: {online} kishi\n"
        f"👑 Jami adminlar: {admins_total} kishi\n"
        f"🟢 Online adminlar: {online_admins} kishi\n\n"
        "ℹ️ \"Chiqib ketganlar\" sonini Telegram bot API aniq bermaydi "
        "(faqat botni bloklaganini bilsa bo'ladi), shu sabab bu ko'rsatkich yo'q.",
        parse_mode="HTML",
    )


@router.message(F.text == "📊 Statistika")
async def btn_stats(message: Message):
    if not is_admin(message.from_user.id):
        return
    animes_count = db("SELECT COUNT(*) FROM animes", (), "one")[0]
    episodes_count = db("SELECT COUNT(*) FROM episodes", (), "one")[0]
    users_count = db("SELECT COUNT(*) FROM users", (), "one")[0]
    admins_count = db("SELECT COUNT(*) FROM admins", (), "one")[0]
    vip_count = db("SELECT COUNT(*) FROM users WHERE is_vip=1", (), "one")[0]
    await message.answer(
        "📊 <b>Bot statistikasi</b>\n\n"
        f"🎬 Animelar: {animes_count}\n"
        f"🎞 Jami qismlar: {episodes_count}\n"
        f"👥 Foydalanuvchilar: {users_count}\n"
        f"👑 Adminlar: {admins_count}\n"
        f"💎 VIP: {vip_count}",
        parse_mode="HTML",
    )


# ============ 3️⃣ ANIME QO'SHISH ============
@router.message(F.text == "3️⃣ Anime qo'shish")
async def btn_add_anime(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "🖼 Anime uchun rasm (poster) yuboring, rasm ostiga (caption) shu shaklda yozing:\n\n"
        "<code>Kod: 25\n"
        "Nomi: Anime nomi\n"
        "Fasllar: 3\n"
        "Qismlar: 12\n"
        "Janr: Isekai, jangari\n"
        "Kanal: @anistarkuz\n"
        "Premium: yo'q</code>\n\n"
        "ℹ️ \"Premium\" qatoriga <code>ha</code> yozsangiz — bu anime faqat VIP foydalanuvchilarga ochiladi. "
        "\"yo'q\" yozsangiz (yoki qatorni tashlab ketsangiz) — hammaga ochiq bo'ladi.\n\n"
        "⚠️ \"Kod\" — ichki, texnik kod, foydalanuvchiga ko'rinmaydi. Har bir kod faqat bitta animega tegishli.",
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
        total_episodes_declared = int(fields.get("qismlar", "0"))
        genre = fields.get("janr", "-")
        channel_name = fields.get("kanal", "")
        is_premium = 1 if fields.get("premium", "yo'q").lower() in ("ha", "premium", "vip") else 0
    except (KeyError, ValueError) as e:
        await message.answer(f"❌ Ma'lumot to'liq emas: {e}")
        return

    existing = db("SELECT title FROM animes WHERE code=?", (code,), "one")
    if existing and existing[0] != title:
        await message.answer(f"⚠️ Bu kod (\"{code}\") allaqachon \"{existing[0]}\" animesi uchun band. Boshqa kod tanlang.")
        return

    poster_file_id = message.photo[-1].file_id
    db("""INSERT OR REPLACE INTO animes
          (code, title, total_seasons, total_episodes_declared, genre, channel_name,
           quality, rating, views, downloads, is_premium, poster_file_id, added_date)
          VALUES (?,?,?,?,?,?,
                  COALESCE((SELECT quality FROM animes WHERE code=?),'-'),
                  COALESCE((SELECT rating FROM animes WHERE code=?),'-'),
                  COALESCE((SELECT views FROM animes WHERE code=?),0),
                  COALESCE((SELECT downloads FROM animes WHERE code=?),0),
                  ?,?,?)""",
       (code, title, total_seasons, total_episodes_declared, genre, channel_name,
        code, code, code, code, is_premium, poster_file_id, datetime.now().isoformat()))

    await message.answer(
        f"✅ Anime qo'shildi!\nKod: <code>{code}</code>\n\n"
        f"Endi \"5️⃣ Qism qo'shish\" tugmasi orqali qismlarini yuklang.",
        parse_mode="HTML",
    )

    if ANNOUNCE_CHANNEL_ID and BOT_USERNAME:
        announce_caption = (
            f"🎬 {title}\n"
            f"🎞 Fasllar: {total_seasons} | Qismlar: {total_episodes_declared}\n"
            f"🏷 Janr: {genre}"
        )
        watch_kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="💈Anime Ko'rish💈", url=f"https://t.me/{BOT_USERNAME}?start={code}")
        ]])
        try:
            await bot.send_photo(int(ANNOUNCE_CHANNEL_ID), poster_file_id,
                                  caption=announce_caption, reply_markup=watch_kb)
        except TelegramBadRequest as e:
            logging.error(f"Announce error: {e}")
            await message.answer("⚠️ Anime qo'shildi, lekin kanalga e'lon qilishda xatolik (bot kanalga admin ekanini tekshiring).")


# ============ 4️⃣ ANIME OLISH (o'chirish) ============
@router.message(F.text == "4️⃣ Anime olish")
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
    await message.answer(f"🗑 \"{row[0]}\" (kod: {code}) butunlay o'chirildi.")


# ============ 5️⃣ QISM QO'SHISH ============
@router.message(F.text == "5️⃣ Qism qo'shish")
async def btn_add_episode(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "➕ Qism qo'shishning 2 usuli bor:\n\n"
        "1) Videoni to'g'ridan-to'g'ri botga yuboring, caption qismiga yozing:\n"
        "<code>/addep KOD FASL QISM\n"
        "Qism nomi (ixtiyoriy)</code>\n\n"
        "2) Videoni (boshqa botdan/kanaldan) botga <b>forward</b> qiling, "
        "so'ng o'sha forward qilingan xabarga <b>javob (reply)</b> tariqasida yozing:\n"
        "<code>/addep KOD FASL QISM</code>",
        parse_mode="HTML",
    )


def _parse_addep_caption(caption: str):
    lines = caption.split("\n")
    parts = lines[0].split()
    if len(parts) != 4:
        return None
    _, code, season, num = parts
    title = lines[1].strip() if len(lines) > 1 else None
    return code, int(season), int(num), title


@router.message(F.video, F.caption, F.caption.startswith("/addep"))
async def process_addep_direct(message: Message):
    if not is_admin(message.from_user.id):
        return
    parsed = _parse_addep_caption(message.caption)
    if not parsed:
        await message.answer("❗️ Format: /addep KOD FASL QISM  (masalan: /addep 25 1 1)")
        return
    code, season, num, ep_title = parsed
    if not db("SELECT 1 FROM animes WHERE code=?", (code,), "one"):
        await message.answer("❌ Bunday kodli anime topilmadi, avval anime qo'shing.")
        return
    file_id = message.video.file_id
    db("INSERT OR REPLACE INTO episodes (anime_code, season_number, episode_number, file_id, episode_title) VALUES (?,?,?,?,?)",
       (code, season, num, file_id, ep_title))
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


# ============ 6️⃣ QISM OLISH ============
@router.message(F.text == "6️⃣ Qism olish")
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


# ============ 7️⃣ KODNI O'ZGARTIRISH ============
@router.message(F.text == "7️⃣ Kodni o'zgartirish")
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
    await message.answer(f"✅ \"{title}\" kodi {old_code} dan {new_code} ga o'zgartirildi.")


# ============ 8️⃣ KODLAR RO'YXATI ============
@router.message(F.text == "8️⃣ Kodlar ro'yxati")
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


# ============ 9️⃣ QISM POST QILISH ============
@router.message(F.text == "9️⃣ Qism post qilish")
async def btn_postep(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "📢 Kanalga post qilmoqchi bo'lgan (allaqachon botga qo'shilgan) qismni "
        "<code>KOD FASL QISM</code> shaklida yuboring (masalan: 25 1 14):",
        parse_mode="HTML",
    )
    await state.set_state(AdminFSM.waiting_postep)


@router.message(StateFilter(AdminFSM.waiting_postep))
async def process_postep(message: Message, state: FSMContext):
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

    anime = db("SELECT title, poster_file_id FROM animes WHERE code=?", (code,), "one")
    if not anime:
        await message.answer("❌ Bunday kodli anime topilmadi.")
        return
    ep = db("SELECT episode_title FROM episodes WHERE anime_code=? AND season_number=? AND episode_number=?",
            (code, season, num), "one")
    if not ep:
        await message.answer("❌ Bu qism hali botga qo'shilmagan. Avval \"5️⃣ Qism qo'shish\" orqali qo'shing.")
        return

    title, poster_file_id = anime
    ep_title = ep[0]
    caption = f"🎬 {title}\n🎞 {season}-fasl, {num}-qism"
    if ep_title:
        caption += f"\n📝 {ep_title}"

    deep_link = f"https://t.me/{BOT_USERNAME}?start={code}_{season}_{num}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"🎰{num}-qism ko'rish🎰", url=deep_link)
    ]])
    try:
        if poster_file_id:
            await bot.send_photo(int(ANNOUNCE_CHANNEL_ID), poster_file_id, caption=caption, reply_markup=kb)
        else:
            await bot.send_message(int(ANNOUNCE_CHANNEL_ID), caption, reply_markup=kb)
        await message.answer("✅ Kanalga post qilindi!")
    except TelegramBadRequest as e:
        logging.error(e)
        await message.answer("⚠️ Kanalga post qilishda xatolik. Bot kanalga admin ekanini tekshiring.")


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


# ============ ADMIN: KANAL REKLAMA (anime kartochkasida ko'rinadigan) ============
@router.message(F.text == "📢 Kanal reklama")
async def btn_ad_channel(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    current = get_setting("ad_channel", "— sozlanmagan —")
    await message.answer(
        f"📢 Hozirgi anime kartochkalarida ko'rsatiladigan kanal: {current}\n\n"
        "Yangi kanal username'ini yuboring (masalan @anistarkuz):"
    )
    await state.set_state(AdminFSM.waiting_ad_channel)


@router.message(StateFilter(AdminFSM.waiting_ad_channel))
async def process_ad_channel(message: Message, state: FSMContext):
    await state.clear()
    set_setting("ad_channel", message.text.strip())
    await message.answer(f"✅ Kanal reklamasi o'rnatildi: {message.text.strip()}")


# ============ ADMIN: VIP BERISH VA XABAR ============
@router.message(Command("givevip"))
async def cmd_givevip(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    if not command.args:
        await message.answer("❗️ Foydalanish: /givevip USER_ID MUDDAT (1oy/2oy/3oy/6oy/8oy/forever)")
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
        await message.answer("❗️ Noto'g'ri muddat. Variantlar: 1oy, 2oy, 3oy, 6oy, 8oy yoki forever")
        return
    ensure_user(target_id, "")
    until = "forever" if days is None else (datetime.now() + timedelta(days=days)).isoformat()
    db("UPDATE users SET is_vip=1, vip_until=? WHERE user_id=?", (until, target_id))
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


# ============ TO'G'RIDAN-TO'G'RI KOD/NOM YOZILSA ============
ADMIN_BUTTON_TEXTS = {
    "1️⃣ Admin qo'shish/olish", "2️⃣ Foydalanuvchilar", "3️⃣ Anime qo'shish", "4️⃣ Anime olish",
    "5️⃣ Qism qo'shish", "6️⃣ Qism olish", "7️⃣ Kodni o'zgartirish", "8️⃣ Kodlar ro'yxati",
    "9️⃣ Qism post qilish", "📢 Kanal reklama", "🔒 Majburiy obuna", "📊 Statistika",
}


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
    db_init()
    await bot.set_my_commands([
        BotCommand(command="start", description="Botni ishga tushirish"),
        BotCommand(command="vip", description="VIP olish"),
        BotCommand(command="profil", description="Profilim"),
        BotCommand(command="tarix", description="Tomosha tarixim"),
    ])
    await start_webserver()
    print("Anime bot ishga tushdi...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
