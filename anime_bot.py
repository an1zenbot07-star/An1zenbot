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
    waiting_episode_post_info = State()
    waiting_episode_post_video = State()


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
        caption TEXT DEFAULT '',
        PRIMARY KEY (anime_code, season_number, episode_number)
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, username TEXT, joined_date TEXT,
        is_vip INTEGER DEFAULT 0, vip_until TEXT, last_seen TEXT,
        is_blocked INTEGER DEFAULT 0, left_date TEXT
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
    # Eski anime_bot.db fayllari uchun xavfsiz migratsiya.
    episode_cols = {row[1] for row in cur.execute("PRAGMA table_info(episodes)").fetchall()}
    if "caption" not in episode_cols:
        cur.execute("ALTER TABLE episodes ADD COLUMN caption TEXT DEFAULT ''")

    user_cols = {row[1] for row in cur.execute("PRAGMA table_info(users)").fetchall()}
    if "is_blocked" not in user_cols:
        cur.execute("ALTER TABLE users ADD COLUMN is_blocked INTEGER DEFAULT 0")
    if "left_date" not in user_cols:
        cur.execute("ALTER TABLE users ADD COLUMN left_date TEXT")

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
    row = db("SELECT 1 FROM users WHERE user_id=?", (user_id,), "one")
    if not row:
        db("""INSERT INTO users
              (user_id, username, joined_date, last_seen, is_blocked, left_date)
              VALUES (?,?,?,?,0,NULL)""",
           (user_id, username or "", now, now))
    else:
        db("""UPDATE users
              SET username=?, last_seen=?, is_blocked=0, left_date=NULL
              WHERE user_id=?""",
           (username or "", now, user_id))


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

    caption = f"🎬 <b>{title}</b>\n"
    caption += f"🎞 Fasllar: {total_seasons}\n"
    if genre and genre != "-":
        caption += f"🏷 Janr: {genre}\n"
    caption += "\n📌 Kerakli fasl va qismni tanlang."

    kb = seasons_keyboard(acode, total_seasons) if total_seasons > 1 else episode_count_kb(acode, 1)

    try:
        if poster_file_id:
            await message.answer_photo(
                poster_file_id, caption=caption, parse_mode="HTML", reply_markup=kb
            )
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
        if not await check_required_channels(message):
            return

        arg = command.args.strip()
        if arg.startswith("ep_"):
            try:
                _, code, season, num = arg.split("_")
                season, num = int(season), int(num)
                ep = db(
                    "SELECT file_id, caption FROM episodes "
                    "WHERE anime_code=? AND season_number=? AND episode_number=?",
                    (code, season, num), "one"
                )
                if ep:
                    await bot.send_video(
                        message.chat.id,
                        ep[0],
                        caption=ep[1] or None
                    )
                else:
                    await message.answer("❌ Bu qism topilmadi.")
                return
            except ValueError:
                pass

        if db("SELECT 1 FROM animes WHERE code=?", (arg,), "one"):
            await show_anime(message, arg, message.from_user.id)
            return

    if is_admin(message.from_user.id):
        await message.answer("🛠 Xush kelibsiz, admin!", reply_markup=admin_menu())
    else:
        await message.answer("Xush kelibsiz!", reply_markup=ReplyKeyboardRemove())


# ============ MAJBURIY OBUNA ============
def required_channels():
    return db("SELECT channel_id, title, link FROM required_channels ORDER BY added_date", (), "all")

async def check_required_channels(message: Message) -> bool:
    channels = required_channels()
    if not channels:
        return True

    missing = await check_required_channels_user(message.from_user.id)
    if not missing:
        return True

    rows = [
        [InlineKeyboardButton(text="✅ Kanal obuna ✅", url=link)]
        for _, link in missing
    ]
    rows.append([
        InlineKeyboardButton(text="🔎 Tekshirish", callback_data="sub:check")
    ])
    await message.answer(
        "⚠️ Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )
    return False


@router.callback_query(F.data == "sub:check")
async def cb_sub_check(callback: CallbackQuery):
    missing = await check_required_channels_user(callback.from_user.id)
    if missing:
        rows = [
            [InlineKeyboardButton(text="✅ Kanal obuna ✅", url=link)]
            for _, link in missing
        ]
        rows.append([
            InlineKeyboardButton(text="🔎 Tekshirish", callback_data="sub:check")
        ])
        try:
            await callback.message.edit_reply_markup(
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
            )
        except TelegramBadRequest:
            pass
        await callback.answer(
            "❌ Hali barcha kanallarga obuna bo'lmagansiz.",
            show_alert=True
        )
    else:
        await callback.message.edit_text(
            "✅ Obuna tasdiqlandi. Endi anime kodini yuborishingiz mumkin."
        )
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

    # Foydalanuvchi keyin kanalni tark etgan bo'lsa, eski tugma orqali ham kira olmaydi.
    missing = await check_required_channels_user(callback.from_user.id)
    if missing:
        rows = [
            [InlineKeyboardButton(text="✅ Kanal obuna ✅", url=link)]
            for _, link in missing
        ]
        rows.append([
            InlineKeyboardButton(text="🔎 Tekshirish", callback_data="sub:check")
        ])
        await callback.message.answer(
            "⚠️ Avval barcha majburiy kanallarga obuna bo'ling.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
        )
        await callback.answer("❌ Majburiy obuna talab qilinadi.", show_alert=True)
        return

    ensure_user(callback.from_user.id, callback.from_user.username)
    ep = db(
        "SELECT file_id, caption FROM episodes "
        "WHERE anime_code=? AND season_number=? AND episode_number=?",
        (code, season, num), "one"
    )
    if not ep:
        await callback.answer("❌ Bu qism hali yuklanmagan.", show_alert=True)
        return

    try:
        await bot.send_video(callback.message.chat.id, ep[0], caption=ep[1] or None)
        db("UPDATE animes SET downloads = downloads + 1 WHERE code=?", (code,))
        db(
            "INSERT OR REPLACE INTO history "
            "(user_id, anime_code, season_number, episode_number, watched_date) "
            "VALUES (?,?,?,?,?)",
            (callback.from_user.id, code, season, num, datetime.now().isoformat())
        )
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
        [InlineKeyboardButton(text="➖ Admin olish", callback_data="adm:remove")],
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

@router.callback_query(F.data == "adm:remove")
async def cb_admin_remove_start(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    await callback.message.answer("Adminlar ro'yxatidan olib tashlamoqchi bo'lgan adminni tanlang:")
    await cb_admin_list(callback)
    await callback.answer()


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
    if not is_admin(message.from_user.id):
        return

    total = db("SELECT COUNT(*) FROM users", (), "one")[0]
    entered = db("SELECT COUNT(*) FROM users WHERE is_blocked=0", (), "one")[0]
    left = db("SELECT COUNT(*) FROM users WHERE is_blocked=1", (), "one")[0]

    cutoff = (datetime.now() - timedelta(minutes=ONLINE_WINDOW_MIN)).isoformat()
    online = db(
        "SELECT COUNT(*) FROM users WHERE is_blocked=0 AND last_seen>=?",
        (cutoff,), "one"
    )[0]

    admins_total = db("SELECT COUNT(*) FROM admins", (), "one")[0]
    online_admins = db(
        """SELECT COUNT(*) FROM users
           WHERE is_blocked=0
             AND user_id IN (SELECT user_id FROM admins)
             AND last_seen>=?""",
        (cutoff,), "one"
    )[0]

    await message.answer(
        "👥 <b>Foydalanuvchilar statistikasi</b>\n\n"
        f"👤 Jami: <b>{total}</b>\n"
        f"🟢 Faol (5 daqiqa): <b>{online}</b>\n"
        f"📥 Kirgan: <b>{entered}</b>\n"
        f"📤 Chiqib ketgan/bloklagan: <b>{left}</b>\n"
        f"👑 Jami adminlar: <b>{admins_total}</b>\n"
        f"🟢 Faol adminlar: <b>{online_admins}</b>\n\n"
        "ℹ️ «Faol» — bot bilan oxirgi 5 daqiqada muloqot qilganlar.",
        parse_mode="HTML"
    )




# ============ ADMIN: ANIME QO'SHISH ============
@router.message(F.text == "🎬 Anime qo'shish")
async def btn_add_anime(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "🖼 Anime posterini yuboring. Captionni o'zingiz xohlagandek yozishingiz mumkin.\n\n"
        "Bot faqat quyidagi 4 qatorni o'qiydi:\n"
        "<code>Kod: 25\n"
        "Nomi: Anime nomi\n"
        "Fasllar: 3\n"
        "Janr: Isekai, jangari</code>\n\n"
        "❌ Sifat, reyting, premium va qism soni so'ralmaydi.\n"
        "➕ Qismlar keyin /addep orqali qo'shiladi.",
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
        genre = fields.get("janr", "")
    except (KeyError, ValueError) as e:
        await message.answer(f"❌ Ma'lumot to'liq emas: {e}")
        return

    existing = db("SELECT title FROM animes WHERE code=?", (code,), "one")
    if existing and existing[0] != title:
        await message.answer(
            f"⚠️ {code} kodi allaqachon «{existing[0]}» anime uchun band. Boshqa kod yozing."
        )
        return

    poster_file_id = message.photo[-1].file_id
    db(
        """INSERT OR REPLACE INTO animes
           (code, title, total_seasons, quality, genre, rating,
            views, downloads, is_premium, poster_file_id, added_date)
           VALUES (?,?,?, '-', ?, '-', 
                   COALESCE((SELECT views FROM animes WHERE code=?),0),
                   COALESCE((SELECT downloads FROM animes WHERE code=?),0),
                   0, ?, ?)""",
        (
            code, title, total_seasons, genre,
            code, code, poster_file_id, datetime.now().isoformat()
        )
    )

    await message.answer(
        f"✅ Anime qo'shildi!\nKod: <code>{code}</code>\nNomi: {title}\n"
        f"Fasllar: {total_seasons}\nJanr: {genre or '—'}",
        parse_mode="HTML",
    )

    if ANNOUNCE_CHANNEL_ID and BOT_USERNAME:
        announce_caption = f"🎬 {title}\n🎞 {total_seasons}-fasl"
        if genre:
            announce_caption += f"\n🏷 {genre}"

        watch_kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="💈Anime Korish💈",
                url=f"https://t.me/{BOT_USERNAME}?start={code}"
            )
        ]])
        try:
            await bot.send_photo(
                int(ANNOUNCE_CHANNEL_ID),
                poster_file_id,
                caption=announce_caption,
                reply_markup=watch_kb
            )
        except TelegramBadRequest as e:
            logging.error(f"Announce error: {e}")
            await message.answer(
                "⚠️ Anime bazaga qo'shildi, lekin kanalga e'lon qilishda xatolik. "
                "Bot kanalga admin ekanini tekshiring."
            )


# ============ ADMIN: QISM QO'SHISH ============
def parse_addep_command(text: str):
    """Returns code, season, episode, custom_caption."""
    lines = text.splitlines()
    first = lines[0].strip() if lines else ""
    parts = first.split()
    if len(parts) != 4 or parts[0].lower() != "/addep":
        return None
    try:
        season = int(parts[2])
        episode = int(parts[3])
    except ValueError:
        return None
    custom_caption = "\n".join(lines[1:]).strip()
    return parts[1], season, episode, custom_caption


async def save_episode(code, season, num, file_id, caption=""):
    if not db("SELECT 1 FROM animes WHERE code=?", (code,), "one"):
        return False
    db(
        """INSERT OR REPLACE INTO episodes
           (anime_code, season_number, episode_number, file_id, caption)
           VALUES (?,?,?,?,?)""",
        (code, season, num, file_id, caption or "")
    )
    return True


@router.message(F.text == "➕ Qism qo'shish")
async def btn_add_episode(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "➕ Qism qo'shish:\n\n"
        "Videoni botga yuboring va captionning 1-qatoriga:\n"
        "<code>/addep KOD FASL QISM</code>\n"
        "deb yozing. Keyingi qatorlarga esa tag/captionni o'zingiz xohlagandek yozing.\n\n"
        "Masalan:\n"
        "<code>/addep 670 1 2\n"
        "🎬 Qayta tug'ilgan aristokratning misli ko'rilmagan sarguzashtlari\n"
        "1-FASL • 2-qism</code>\n\n"
        "Forward qilingan videoga reply qilib ham shu formatdan foydalanishingiz mumkin.",
        parse_mode="HTML",
    )


@router.message(F.video, F.caption)
async def process_addep_direct(message: Message):
    if not is_admin(message.from_user.id):
        return
    parsed = parse_addep_command(message.caption or "")
    if not parsed:
        return

    code, season, num, custom_caption = parsed
    if not await save_episode(code, season, num, message.video.file_id, custom_caption):
        await message.answer("❌ Bunday kodli anime topilmadi, avval anime qo'shing.")
        return

    await message.answer(
        f"✅ {code}-anime, {season}-fasl, {num}-qism qo'shildi!"
        + ("\n📝 Caption/tag ham saqlandi." if custom_caption else "")
    )


@router.message(Command("addep"))
async def cmd_addep_reply(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return

    if not message.reply_to_message or not message.reply_to_message.video:
        await message.answer(
            "❗️ Videoga reply qilib yozing:\n"
            "<code>/addep KOD FASL QISM</code>\n"
            "Keyingi qatorlarda o'zingiz xohlagan caption/tagni yozishingiz mumkin.",
            parse_mode="HTML",
        )
        return

    raw = message.text or ""
    parsed = parse_addep_command(raw)
    if not parsed:
        await message.answer(
            "❗️ Format:\n<code>/addep KOD FASL QISM</code>",
            parse_mode="HTML",
        )
        return

    code, season, num, custom_caption = parsed
    file_id = message.reply_to_message.video.file_id

    if not await save_episode(code, season, num, file_id, custom_caption):
        await message.answer("❌ Bunday kodli anime topilmadi.")
        return

    await message.answer(
        f"✅ {code}-anime, {season}-fasl, {num}-qism qo'shildi!"
        + ("\n📝 Caption/tag ham saqlandi." if custom_caption else "")
    )


# ============ ADMIN: ANIME / QISM O'CHIRISH ============
@router.message(F.text == "🗑 Anime olish")
async def btn_delete_anime(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await message.answer("Anime kodini yuboring:")
    await state.set_state(AdminFSM.waiting_delete_target)


@router.message(F.text == "🗑 Qism olish")
async def btn_delete_episode(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await message.answer("Format: KOD FASL QISM\nMasalan: <code>670 1 14</code>", parse_mode="HTML")
    await state.set_state(AdminFSM.waiting_delete_target)


@router.message(StateFilter(AdminFSM.waiting_delete_target))
async def process_delete(message: Message, state: FSMContext):
    await state.clear()
    parts = (message.text or "").strip().split()

    if len(parts) == 3:
        code, season, num = parts
        try:
            season, num = int(season), int(num)
        except ValueError:
            await message.answer("❗️ Fasl va qism raqam bo'lishi kerak.")
            return

        row = db(
            "SELECT 1 FROM episodes WHERE anime_code=? AND season_number=? AND episode_number=?",
            (code, season, num), "one"
        )
        if not row:
            await message.answer("❌ Bu qism topilmadi.")
            return

        db(
            "DELETE FROM episodes WHERE anime_code=? AND season_number=? AND episode_number=?",
            (code, season, num)
        )
        await message.answer(f"🗑 {code}-anime, {season}-fasl, {num}-qism o'chirildi.")
        return

    if len(parts) != 1:
        await message.answer("❗️ Faqat anime kodi yoki KOD FASL QISM yozing.")
        return

    query = parts[0]
    exact = db("SELECT code, title FROM animes WHERE code=?", (query,), "one")
    if not exact:
        await message.answer("❌ Bunday anime kodi topilmadi.")
        return

    code = exact[0]
    db("DELETE FROM animes WHERE code=?", (code,))
    db("DELETE FROM episodes WHERE anime_code=?", (code,))
    db("DELETE FROM history WHERE anime_code=?", (code,))
    await message.answer(f"🗑 «{exact[1]}» (kod: {code}) butunlay o'chirildi.")


# ============ TO'G'RIDAN-TO'G'RI KOD YOZILSA ============
@router.message(F.text & ~F.text.startswith("/"))
async def fallback_text(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        return

    ensure_user(message.from_user.id, message.from_user.username)

    admin_buttons = {
        "👑 Admin huquqlari", "👥 Foydalanuvchilar",
        "🎬 Anime qo'shish", "🗑 Anime olish",
        "➕ Qism qo'shish", "🗑 Qism olish",
        "🔄 Kodni o'zgartirish", "📋 Kodlar ro'yxati",
        "📢 Qism post qilish", "🔔 Majburiy obuna",
    }
    if message.text in admin_buttons:
        return

    await _do_search(message, message.text.strip())


# ============ ADMIN: KODNI O'ZGARTIRISH ============
@router.message(F.text == "🔄 Kodni o'zgartirish")
async def btn_code_change(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "Avval eski kodni, keyin yangi kodni yuboring.\n"
        "Masalan: <code>670 999</code>",
        parse_mode="HTML",
    )
    await state.set_state(AdminFSM.waiting_code_change)


@router.message(StateFilter(AdminFSM.waiting_code_change))
async def process_code_change(message: Message, state: FSMContext):
    await state.clear()
    parts = (message.text or "").split()
    if len(parts) != 2:
        await message.answer("❗️ Format: ESKI_KOD YANGI_KOD")
        return

    old, new = parts
    if not db("SELECT 1 FROM animes WHERE code=?", (old,), "one"):
        await message.answer("❌ Eski kod topilmadi.")
        return
    if db("SELECT 1 FROM animes WHERE code=?", (new,), "one"):
        await message.answer("❌ Yangi kod band.")
        return

    db("UPDATE animes SET code=? WHERE code=?", (new, old))
    db("UPDATE episodes SET anime_code=? WHERE anime_code=?", (new, old))
    db("UPDATE history SET anime_code=? WHERE anime_code=?", (new, old))
    await message.answer(f"✅ Kod o'zgartirildi: {old} → {new}")


@router.message(F.text == "📋 Kodlar ro'yxati")
async def btn_code_list(message: Message):
    if not is_admin(message.from_user.id):
        return
    rows = db("SELECT code, title FROM animes ORDER BY added_date DESC", (), "all")
    if not rows:
        await message.answer("📋 Kodlar ro'yxati bo'sh.")
        return

    text = "📋 <b>Botga joylangan animelar:</b>\n\n"
    text += "\n".join(f"🎬 <b>{t}</b> — kod: <code>{c}</code>" for c, t in rows)
    await message.answer(text, parse_mode="HTML")


# ============ ADMIN: QISM POST QILISH ============
@router.message(F.text == "📢 Qism post qilish")
async def btn_episode_post(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.set_state(AdminFSM.waiting_episode_post_info)
    await message.answer(
        "📢 Qism post qilish uchun avval:\n"
        "<code>KOD FASL QISM</code> yuboring.\n\n"
        "Masalan: <code>670 1 14</code>\n"
        "Keyin video yuboring yoki forward qiling."
        ,
        parse_mode="HTML",
    )


@router.message(StateFilter(AdminFSM.waiting_episode_post_info))
async def process_episode_post_info(message: Message, state: FSMContext):
    parts = (message.text or "").split()
    if len(parts) != 3:
        await message.answer("❗️ Format: KOD FASL QISM")
        return

    code, season, num = parts
    try:
        season, num = int(season), int(num)
    except ValueError:
        await message.answer("❗️ Fasl va qism raqam bo'lishi kerak.")
        return

    anime = db("SELECT title FROM animes WHERE code=?", (code,), "one")
    if not anime:
        await message.answer("❌ Bunday anime kodi topilmadi.")
        return

    await state.update_data(post_code=code, post_season=season, post_num=num)
    await state.set_state(AdminFSM.waiting_episode_post_video)
    await message.answer("🎥 Endi qism videosini yuboring yoki forward qiling.")


async def publish_episode_to_channel(
    admin_message: Message, code: str, season: int, num: int,
    file_id: str, custom_caption: str = ""
):
    anime = db("SELECT title FROM animes WHERE code=?", (code,), "one")
    if not anime:
        await admin_message.answer("❌ Anime topilmadi.")
        return

    # Bazaga ham saqlanadi, shunda kanal tugmasi aynan shu qismni ochadi.
    await save_episode(code, season, num, file_id, custom_caption)

    caption = f"🎬 {anime[0]}\n🎞 {season}-fasl • {num}-qism"
    if custom_caption:
        caption += f"\n\n{custom_caption}"

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text=f"🎰{num} - qism ko‘rish🎰",
            url=f"https://t.me/{BOT_USERNAME}?start=ep_{code}_{season}_{num}"
        )
    ]])

    try:
        await bot.send_video(
            int(ANNOUNCE_CHANNEL_ID),
            file_id,
            caption=caption,
            reply_markup=kb
        )
        await admin_message.answer("✅ Qism kanalga post qilindi.")
    except Exception as e:
        logging.exception(e)
        await admin_message.answer(
            "❌ Kanalga post qilishda xatolik. Bot kanalga admin ekanini tekshiring."
        )


@router.message(StateFilter(AdminFSM.waiting_episode_post_video), F.video)
async def process_episode_post_video(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()

    code = data.get("post_code")
    season = data.get("post_season")
    num = data.get("post_num")
    if not code:
        await message.answer("❌ Post ma'lumotlari topilmadi. Qaytadan boshlang.")
        return

    # Video caption bo'lsa, u kanal postining tag/caption qismi sifatida ham ishlatiladi.
    custom_caption = (message.caption or "").strip()
    await publish_episode_to_channel(
        message, code, int(season), int(num), message.video.file_id, custom_caption
    )


@router.message(Command("postep"))
async def cmd_postep(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return

    if not message.reply_to_message or not message.reply_to_message.video or not command.args:
        await message.answer("Format: video xabariga reply → /postep KOD FASL QISM")
        return

    parts = command.args.split()
    if len(parts) != 3:
        await message.answer("❗️ Format: /postep KOD FASL QISM")
        return

    code, season, num = parts
    try:
        season, num = int(season), int(num)
    except ValueError:
        await message.answer("❗️ Fasl va qism raqam bo'lishi kerak.")
        return

    await publish_episode_to_channel(
        message, code, season, num,
        message.reply_to_message.video.file_id,
        (message.text or "").splitlines()[1:] and "\n".join((message.text or "").splitlines()[1:]).strip()
    )


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

# ============ FOYDALANUVCHI KIRDI/CHIQDI ============
@router.my_chat_member()
async def bot_chat_member_update(event):
    try:
        user_id = event.chat.id
        username = getattr(event.chat, "username", None)
        new_status = event.new_chat_member.status
        now = datetime.now().isoformat()

        if new_status in ("kicked", "left"):
            db(
                "UPDATE users SET is_blocked=1, left_date=? WHERE user_id=?",
                (now, user_id)
            )
        elif new_status in ("member", "administrator"):
            ensure_user(user_id, username)
    except Exception as e:
        logging.error(f"User status update error: {e}")


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
