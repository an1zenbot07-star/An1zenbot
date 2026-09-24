import asyncio
import logging
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from html import escape

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatMemberStatus
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder


# ============================================================
# CONFIG
# ============================================================
# Render: add BOT_TOKEN as an Environment Variable.
# ADMIN_ID = your Telegram numeric ID.
# CHANNEL_ID = the anime channel where new anime posts are sent.
# CHANNEL_LINK = public channel link.
#
# This file is reusable: copy it for another bot, change BOT_TOKEN,
# ADMIN_ID and CHANNEL_ID, and optionally DB_PATH.
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()
CHANNEL_LINK = os.getenv("CHANNEL_LINK", "").strip()
DB_PATH = os.getenv("DB_PATH", "anime_bot.db")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required.")
if not ADMIN_ID:
    raise RuntimeError("ADMIN_ID environment variable is required.")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("anime_pro_bot")

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()
router = Router()
dp.include_router(router)


# ============================================================
# DATABASE
# ============================================================
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row


def now():
    return datetime.now(timezone.utc).isoformat()


def init_db():
    db.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        joined_at TEXT NOT NULL,
        last_seen TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS admins (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        added_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS animes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        seasons TEXT NOT NULL,
        episodes_total INTEGER NOT NULL DEFAULT 0,
        genre TEXT NOT NULL,
        created_at TEXT NOT NULL,
        channel_message_id INTEGER
    );

    CREATE TABLE IF NOT EXISTS episodes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        anime_id INTEGER NOT NULL,
        season INTEGER NOT NULL,
        number INTEGER NOT NULL,
        file_id TEXT NOT NULL,
        caption TEXT NOT NULL DEFAULT '',
        views INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        UNIQUE(anime_id, season, number),
        FOREIGN KEY(anime_id) REFERENCES animes(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS required_channels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_ref TEXT UNIQUE NOT NULL,
        title TEXT NOT NULL,
        invite_link TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """)
    db.execute(
        "INSERT OR IGNORE INTO admins(user_id, username, added_at) VALUES (?, ?, ?)",
        (ADMIN_ID, "", now())
    )
    db.commit()


def is_admin(user_id: int) -> bool:
    row = db.execute(
        "SELECT 1 FROM admins WHERE user_id = ?", (user_id,)
    ).fetchone()
    return row is not None


def upsert_user(message: Message):
    u = message.from_user
    t = now()
    db.execute("""
        INSERT INTO users(user_id, username, first_name, joined_at, last_seen)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username=excluded.username,
            first_name=excluded.first_name,
            last_seen=excluded.last_seen
    """, (
        u.id,
        u.username or "",
        u.first_name or "",
        t,
        t,
    ))
    db.commit()


# ============================================================
# KEYBOARDS
# ============================================================
def user_start_keyboard():
    # User's normal reply menu intentionally has only Start.
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="/start")]],
        resize_keyboard=True
    )


def admin_keyboard():
    b = ReplyKeyboardBuilder()
    b.button(text="👑 Admin huquqlari")
    b.button(text="🎬 Anime qo'shish")
    b.button(text="🗑 Anime olish")
    b.button(text="➕ Qism qo'shish")
    b.button(text="🗑 Qism olish")
    b.button(text="👥 Foydalanuvchilar")
    b.button(text="📊 Statistika")
    b.button(text="🔔 Majburiy obuna")
    b.adjust(2, 2, 2, 1)
    return b.as_markup(resize_keyboard=True)


def admin_rights_keyboard():
    b = InlineKeyboardBuilder()
    b.button(text="➕ Admin qo'shish", callback_data="admin:add")
    b.button(text="➖ Admin olish", callback_data="admin:remove")
    b.button(text="📋 Adminlar", callback_data="admin:list")
    b.adjust(1)
    return b.as_markup()


def subscription_keyboard(channels):
    b = InlineKeyboardBuilder()
    for ch in channels:
        b.button(
            text="✅ Obuna bo'lish",
            url=ch["invite_link"]
        )
    b.button(text="🔄 Tekshirish", callback_data="sub:check")
    b.adjust(1)
    return b.as_markup()


def anime_view_keyboard(code: str, bot_username: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="💈Anime Korish💈",
                url=f"https://t.me/{bot_username}?start={code}"
            )
        ]]
    )


def seasons_keyboard(code: str, seasons_text: str):
    values = parse_seasons(seasons_text)
    b = InlineKeyboardBuilder()
    for s in values:
        b.button(text=f"{s}-fasl", callback_data=f"season:{code}:{s}")
    b.adjust(2)
    return b.as_markup()


def episodes_keyboard(code: str, season: int, numbers):
    b = InlineKeyboardBuilder()
    for n in numbers:
        b.button(
            text=f"🎰{n} - qism ko'rish🎰",
            callback_data=f"ep:{code}:{season}:{n}"
        )
    b.adjust(2)
    return b.as_markup()


# ============================================================
# HELPERS
# ============================================================
def parse_seasons(text: str):
    text = text.strip().lower().replace("fasl", "").replace(" ", "")
    if "-" in text:
        a, b = text.split("-", 1)
        try:
            return list(range(int(a), int(b) + 1))
        except ValueError:
            pass
    if "," in text:
        out = []
        for x in text.split(","):
            try:
                out.append(int(x))
            except ValueError:
                pass
        return out
    try:
        n = int(text)
        return list(range(1, n + 1))
    except ValueError:
        return [1]


def parse_admin_target(text: str):
    text = text.strip()
    if text.startswith("@"):
        return text
    try:
        return int(text)
    except ValueError:
        return None


async def resolve_user_id(value):
    target = parse_admin_target(value)
    if target is None:
        return None
    if isinstance(target, int):
        return target
    try:
        chat = await bot.get_chat(target)
        return chat.id
    except Exception:
        return None


async def get_required_channels():
    return db.execute(
        "SELECT * FROM required_channels ORDER BY id"
    ).fetchall()


async def missing_subscriptions(user_id: int):
    missing = []
    for ch in await get_required_channels():
        try:
            member = await bot.get_chat_member(ch["chat_ref"], user_id)
            if member.status in {
                ChatMemberStatus.LEFT,
                ChatMemberStatus.KICKED,
            }:
                missing.append(ch)
        except Exception as e:
            log.warning("Subscription check failed for %s: %s", ch["chat_ref"], e)
            # If Telegram cannot check a channel, do not silently bypass it.
            missing.append(ch)
    return missing


async def send_subscription_gate(message: Message):
    channels = await get_required_channels()
    if not channels:
        return False

    missing = await missing_subscriptions(message.from_user.id)
    if not missing:
        return False

    text = "📢 <b>Botdan foydalanish uchun kanallarga obuna bo'ling.</b>\n\n"
    for i, ch in enumerate(missing, 1):
        text += f"{i}. {escape(ch['title'])}\n"

    await message.answer(
        text,
        reply_markup=subscription_keyboard(missing)
    )
    return True


async def require_access(message: Message):
    return not await send_subscription_gate(message)


async def bot_username():
    me = await bot.get_me()
    return me.username


async def anime_by_code(code: str):
    return db.execute(
        "SELECT * FROM animes WHERE code = ?", (code,)
    ).fetchone()


async def episode_row(code: str, season: int, number: int):
    return db.execute("""
        SELECT e.*, a.code, a.name
        FROM episodes e
        JOIN animes a ON a.id=e.anime_id
        WHERE a.code=? AND e.season=? AND e.number=?
    """, (code, season, number)).fetchone()


async def episode_numbers(code: str, season: int):
    rows = db.execute("""
        SELECT e.number
        FROM episodes e
        JOIN animes a ON a.id=e.anime_id
        WHERE a.code=? AND e.season=?
        ORDER BY e.number
    """, (code, season)).fetchall()
    return [r["number"] for r in rows]


async def send_anime_info(message: Message, anime):
    eps = db.execute("""
        SELECT season, COUNT(*) c
        FROM episodes
        WHERE anime_id=?
        GROUP BY season
        ORDER BY season
    """, (anime["id"],)).fetchall()

    if not eps:
        await message.answer(
            f"🎬 <b>{escape(anime['name'])}</b>\n"
            f"🔑 Kod: <code>{escape(anime['code'])}</code>\n"
            f"📚 Fasl: {escape(anime['seasons'])}\n"
            f"🎭 Janr: {escape(anime['genre'])}\n\n"
            f"⚠️ Hozircha qismlar qo'shilmagan."
        )
        return

    text = (
        f"🎬 <b>{escape(anime['name'])}</b>\n"
        f"🔑 Kod: <code>{escape(anime['code'])}</code>\n"
        f"🎭 Janr: {escape(anime['genre'])}\n\n"
        f"📚 Faslni tanlang:"
    )
    await message.answer(
        text,
        reply_markup=seasons_keyboard(anime["code"], anime["seasons"])
    )


# ============================================================
# FSM STATES
# ============================================================
class AnimeAdd(StatesGroup):
    code = State()
    name = State()
    seasons = State()
    episodes = State()
    genre = State()


class AnimeDelete(StatesGroup):
    code = State()


class EpisodeAdd(StatesGroup):
    command = State()
    video = State()


class EpisodeDelete(StatesGroup):
    command = State()


class AdminAdd(StatesGroup):
    target = State()


class AdminRemove(StatesGroup):
    target = State()


class ChannelAdd(StatesGroup):
    reference = State()
    link = State()


class ChannelRemove(StatesGroup):
    target = State()


# ============================================================
# /START AND USER SEARCH
# ============================================================
@router.message(Command("start"))
async def start_handler(message: Message, state: FSMContext, command: CommandObject):
    await state.clear()
    upsert_user(message)

    if is_admin(message.from_user.id) and not command.args:
        await message.answer(
            "👑 <b>Admin panel</b>",
            reply_markup=admin_keyboard()
        )
        return

    if await send_subscription_gate(message):
        return

    if command.args:
        arg = command.args.strip()

        # Exact episode deep-link:
        # ep_CODE_SEASON_EP
        if arg.startswith("ep_"):
            parts = arg.split("_")
            if len(parts) == 4:
                _, code, season_s, number_s = parts
                try:
                    season = int(season_s)
                    number = int(number_s)
                except ValueError:
                    await message.answer("❌ Qism havolasi noto'g'ri.")
                    return
                row = await episode_row(code, season, number)
                if not row:
                    await message.answer("❌ Bu qism topilmadi.")
                    return

                db.execute(
                    "UPDATE episodes SET views=views+1 WHERE id=?",
                    (row["id"],)
                )
                db.commit()

                caption = row["caption"] or (
                    f"🎬 {escape(row['name'])}\n"
                    f"📺 {season}-fasl, {number}-qism"
                )
                await message.answer_video(
                    row["file_id"],
                    caption=caption
                )
                return

        anime = await anime_by_code(arg)
        if anime:
            await send_anime_info(message, anime)
            return

        await message.answer("❌ Bunday anime kodi topilmadi.")
        return

    await message.answer(
        "👋 <b>Xush kelibsiz!</b>\n\n"
        "Anime kodini yuboring.",
        reply_markup=user_start_keyboard()
    )


@router.callback_query(F.data == "sub:check")
async def subscription_check(callback: CallbackQuery):
    upsert_user(callback.message)
    missing = await missing_subscriptions(callback.from_user.id)
    if missing:
        await callback.answer("❌ Hali barcha kanallarga obuna bo'lmagansiz.", show_alert=True)
        await callback.message.edit_reply_markup(
            reply_markup=subscription_keyboard(missing)
        )
        return

    await callback.answer("✅ Obuna tasdiqlandi!")
    await callback.message.edit_text(
        "✅ <b>Obuna tasdiqlandi.</b>\n\nAnime kodini yuboring."
    )


@router.callback_query(F.data.startswith("season:"))
async def season_handler(callback: CallbackQuery):
    await callback.answer()
    _, code, season_s = callback.data.split(":", 2)
    season = int(season_s)

    if await missing_subscriptions(callback.from_user.id):
        await callback.message.answer(
            "❌ Avval majburiy kanallarga obuna bo'ling."
        )
        return

    numbers = await episode_numbers(code, season)
    if not numbers:
        await callback.message.answer("❌ Bu faslda qismlar topilmadi.")
        return

    await callback.message.answer(
        f"📺 <b>{season}-fasl</b>\n\nQismni tanlang:",
        reply_markup=episodes_keyboard(code, season, numbers)
    )


@router.callback_query(F.data.startswith("ep:"))
async def episode_callback(callback: CallbackQuery):
    _, code, season_s, number_s = callback.data.split(":", 3)
    season, number = int(season_s), int(number_s)

    if await missing_subscriptions(callback.from_user.id):
        await callback.answer("❌ Obunani tekshiring.", show_alert=True)
        return

    row = await episode_row(code, season, number)
    if not row:
        await callback.answer("❌ Qism topilmadi.", show_alert=True)
        return

    db.execute(
        "UPDATE episodes SET views=views+1 WHERE id=?", (row["id"],)
    )
    db.commit()

    await callback.answer()
    caption = row["caption"] or (
        f"🎬 {escape(row['name'])}\n"
        f"📺 {season}-fasl, {number}-qism"
    )
    await callback.message.answer_video(
        row["file_id"],
        caption=caption
    )


@router.message(F.text)
async def code_search(message: Message):
    upsert_user(message)

    # Admin keyboard buttons are handled separately below.
    if message.text.startswith("/"):
        return
    if message.text in {
        "👑 Admin huquqlari", "🎬 Anime qo'shish", "🗑 Anime olish",
        "➕ Qism qo'shish", "🗑 Qism olish", "👥 Foydalanuvchilar",
        "📊 Statistika", "🔔 Majburiy obuna"
    }:
        return

    if await send_subscription_gate(message):
        return

    code = message.text.strip()
    anime = await anime_by_code(code)
    if not anime:
        await message.answer("❌ Bunday anime kodi topilmadi.")
        return
    await send_anime_info(message, anime)


# ============================================================
# ADMIN ACCESS
# ============================================================
async def deny_non_admin(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Sizda admin huquqi yo'q.")
        return True
    return False


@router.message(Command("panel"))
async def panel_command(message: Message, state: FSMContext):
    if await deny_non_admin(message):
        return
    await state.clear()
    await message.answer("👑 <b>Admin panel</b>", reply_markup=admin_keyboard())


# ============================================================
# ADMIN RIGHTS
# ============================================================
@router.message(F.text == "👑 Admin huquqlari")
async def admin_rights(message: Message):
    if await deny_non_admin(message):
        return
    await message.answer(
        "👑 <b>Admin huquqlari</b>",
        reply_markup=admin_rights_keyboard()
    )


@router.callback_query(F.data == "admin:add")
async def admin_add_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return
    await state.set_state(AdminAdd.target)
    await callback.message.answer(
        "➕ Admin qo'shish.\n\n"
        "Telegram ID yoki @username yuboring:"
    )
    await callback.answer()


@router.message(AdminAdd.target)
async def admin_add_finish(message: Message, state: FSMContext):
    if await deny_non_admin(message):
        return
    target = await resolve_user_id(message.text)
    if target is None:
        await message.answer("❌ ID yoki @username noto'g'ri.")
        return
    try:
        chat = await bot.get_chat(target)
        username = chat.username or ""
    except Exception:
        username = message.text.strip()

    db.execute(
        "INSERT OR IGNORE INTO admins(user_id, username, added_at) VALUES (?, ?, ?)",
        (target, username, now())
    )
    db.execute(
        "UPDATE admins SET username=? WHERE user_id=?",
        (username, target)
    )
    db.commit()
    await state.clear()
    await message.answer(
        f"✅ Admin qo'shildi.\n\nID: <code>{target}</code>\n"
        f"Username: @{username}" if username else
        f"✅ Admin qo'shildi.\n\nID: <code>{target}</code>"
    )


@router.callback_query(F.data == "admin:remove")
async def admin_remove_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return
    await state.set_state(AdminRemove.target)
    await callback.message.answer("➖ Olinadigan admin ID yoki @username:")
    await callback.answer()


@router.message(AdminRemove.target)
async def admin_remove_finish(message: Message, state: FSMContext):
    if await deny_non_admin(message):
        return
    target = await resolve_user_id(message.text)
    if target is None:
        await message.answer("❌ ID yoki @username noto'g'ri.")
        return
    if target == ADMIN_ID:
        await message.answer("❌ Asosiy adminni olib bo'lmaydi.")
        return

    db.execute("DELETE FROM admins WHERE user_id=?", (target,))
    db.commit()
    await state.clear()
    await message.answer("✅ Admin olib tashlandi.")


@router.callback_query(F.data == "admin:list")
async def admin_list(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return
    rows = db.execute(
        "SELECT * FROM admins ORDER BY added_at"
    ).fetchall()
    text = "👑 <b>Adminlar</b>\n\n"
    for i, r in enumerate(rows, 1):
        username = f"@{r['username']}" if r["username"] else "username yo'q"
        text += f"{i}. {username}\nID: <code>{r['user_id']}</code>\n\n"
    await callback.message.answer(text)
    await callback.answer()


# ============================================================
# ANIME ADD
# ============================================================
@router.message(F.text == "🎬 Anime qo'shish")
async def anime_add_start(message: Message, state: FSMContext):
    if await deny_non_admin(message):
        return
    await state.set_state(AnimeAdd.code)
    await message.answer("🎬 Anime qo'shish.\n\n1️⃣ Kodni yuboring:")


@router.message(AnimeAdd.code)
async def anime_add_code(message: Message, state: FSMContext):
    code = message.text.strip()
    if not code or " " in code:
        await message.answer("❌ Kod bitta so'z bo'lsin.")
        return
    if await anime_by_code(code):
        await message.answer("❌ Bu kod allaqachon mavjud.")
        return
    await state.update_data(code=code)
    await state.set_state(AnimeAdd.name)
    await message.answer("2️⃣ Anime nomini yuboring:")


@router.message(AnimeAdd.name)
async def anime_add_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await state.set_state(AnimeAdd.seasons)
    await message.answer(
        "3️⃣ Fasllar sonini yozing.\n"
        "Masalan: <code>3</code> yoki <code>1-3</code>"
    )


@router.message(AnimeAdd.seasons)
async def anime_add_seasons(message: Message, state: FSMContext):
    seasons = message.text.strip()
    parsed = parse_seasons(seasons)
    if not parsed:
        await message.answer("❌ Fasl formati noto'g'ri.")
        return
    await state.update_data(seasons=seasons)
    await state.set_state(AnimeAdd.episodes)
    await message.answer(
        "4️⃣ Umumiy qism sonini yozing.\n"
        "Masalan: <code>24</code>"
    )


@router.message(AnimeAdd.episodes)
async def anime_add_episodes(message: Message, state: FSMContext):
    try:
        episodes = int(message.text.strip())
        if episodes < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Qism soni raqam bo'lishi kerak.")
        return
    await state.update_data(episodes=episodes)
    await state.set_state(AnimeAdd.genre)
    await message.answer("5️⃣ Janrini yuboring:")


@router.message(AnimeAdd.genre)
async def anime_add_genre(message: Message, state: FSMContext):
    data = await state.get_data()
    genre = message.text.strip()

    db.execute("""
        INSERT INTO animes(code, name, seasons, episodes_total, genre, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        data["code"],
        data["name"],
        data["seasons"],
        data["episodes"],
        genre,
        now()
    ))
    db.commit()

    anime = await anime_by_code(data["code"])
    username = await bot_username()

    caption = (
        f"🎬 <b>{escape(data['name'])}</b>\n\n"
        f"🔑 Kod: <code>{escape(data['code'])}</code>\n"
        f"📚 Fasllar: {escape(data['seasons'])}\n"
        f"🎞 Qismlar: {data['episodes']}\n"
        f"🎭 Janr: {escape(genre)}"
    )

    if CHANNEL_ID:
        try:
            sent = await bot.send_message(
                CHANNEL_ID,
                caption,
                reply_markup=anime_view_keyboard(data["code"], username)
            )
            db.execute(
                "UPDATE animes SET channel_message_id=? WHERE id=?",
                (sent.message_id, anime["id"])
            )
            db.commit()
            channel_result = "\n📢 Kanalga avtomatik joylandi."
        except Exception as e:
            log.exception("Channel post failed")
            channel_result = f"\n⚠️ Kanalga joylashda xato: {e}"
    else:
        channel_result = "\n⚠️ CHANNEL_ID sozlanmagan."

    await state.clear()
    await message.answer(
        "✅ <b>Anime qo'shildi!</b>\n\n"
        f"{caption}{channel_result}",
        reply_markup=admin_keyboard()
    )


# ============================================================
# ANIME DELETE
# ============================================================
@router.message(F.text == "🗑 Anime olish")
async def anime_delete_start(message: Message, state: FSMContext):
    if await deny_non_admin(message):
        return
    await state.set_state(AnimeDelete.code)
    await message.answer("🗑 O'chiriladigan anime kodini yuboring:")


@router.message(AnimeDelete.code)
async def anime_delete_finish(message: Message, state: FSMContext):
    if await deny_non_admin(message):
        return
    code = message.text.strip()
    anime = await anime_by_code(code)
    if not anime:
        await message.answer("❌ Anime topilmadi.")
        return

    db.execute("DELETE FROM episodes WHERE anime_id=?", (anime["id"],))
    db.execute("DELETE FROM animes WHERE id=?", (anime["id"],))
    db.commit()
    await state.clear()
    await message.answer(
        f"✅ <b>{escape(anime['name'])}</b> o'chirildi.",
        reply_markup=admin_keyboard()
    )


# ============================================================
# EPISODE ADD
# ============================================================
@router.message(F.text == "➕ Qism qo'shish")
async def episode_add_start(message: Message, state: FSMContext):
    if await deny_non_admin(message):
        return
    await state.set_state(EpisodeAdd.command)
    await message.answer(
        "➕ Qism qo'shish.\n\n"
        "Videoga reply qilib quyidagi buyruqni yuboring:\n\n"
        "<code>/addep KOD FASL QISM</code>\n\n"
        "Caption uchun buyruqdan keyin matn yozishingiz mumkin:\n"
        "<code>/addep 123 1 4 Bu qismning captioni</code>\n\n"
        "Yoki buyruqni yuborib, keyin video yuborishingiz mumkin."
    )


@router.message(Command("addep"))
async def addep_command(message: Message, command: CommandObject, state: FSMContext):
    if await deny_non_admin(message):
        return

    args = (command.args or "").strip()
    parts = args.split(maxsplit=3)
    if len(parts) < 3:
        await message.answer(
            "❌ Format:\n<code>/addep KOD FASL QISM [CAPTION]</code>"
        )
        return

    code, season_s, number_s = parts[0], parts[1], parts[2]
    extra_caption = parts[3] if len(parts) == 4 else ""

    try:
        season = int(season_s)
        number = int(number_s)
        if season < 1 or number < 1:
            raise ValueError
    except ValueError:
        await message.answer("❌ Fasl va qism musbat raqam bo'lishi kerak.")
        return

    anime = await anime_by_code(code)
    if not anime:
        await message.answer("❌ Bunday anime kodi yo'q.")
        return

    await state.update_data(
        code=code,
        season=season,
        number=number,
        caption=extra_caption
    )

    # If command is a reply to a video, save it immediately.
    if message.reply_to_message and message.reply_to_message.video:
        await save_episode(
            admin_message=message,
            video=message.reply_to_message.video,
            state=state
        )
        return

    await state.set_state(EpisodeAdd.video)
    await message.answer(
        "🎞 Endi videoni yuboring.\n"
        "Bot uni shu kod/fasl/qismga saqlaydi."
    )


@router.message(EpisodeAdd.command)
async def episode_add_state_command(message: Message, state: FSMContext):
    # This state mainly exists for the menu flow.
    if message.text and message.text.startswith("/addep"):
        await addep_command(message, CommandObject(args=message.text.partition(" ")[2]), state)
    else:
        await message.answer(
            "Videoga reply qilib <code>/addep KOD FASL QISM</code> yuboring."
        )


async def save_episode(admin_message: Message, video, state: FSMContext):
    data = await state.get_data()
    code = data["code"]
    season = data["season"]
    number = data["number"]
    caption = data.get("caption", "")

    anime = await anime_by_code(code)
    if not anime:
        await admin_message.answer("❌ Anime topilmadi.")
        await state.clear()
        return

    db.execute("""
        INSERT INTO episodes(anime_id, season, number, file_id, caption, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(anime_id, season, number) DO UPDATE SET
            file_id=excluded.file_id,
            caption=excluded.caption
    """, (
        anime["id"], season, number, video.file_id, caption, now()
    ))
    db.commit()
    await state.clear()

    await admin_message.answer(
        f"✅ <b>{escape(anime['name'])}</b>\n"
        f"📺 {season}-fasl, {number}-qism saqlandi.\n"
        f"📝 Caption: {'bor' if caption else 'yo‘q'}",
        reply_markup=admin_keyboard()
    )


@router.message(EpisodeAdd.video, F.video)
async def episode_add_video(message: Message, state: FSMContext):
    await save_episode(message, message.video, state)


# ============================================================
# EPISODE DELETE
# ============================================================
@router.message(F.text == "🗑 Qism olish")
async def episode_delete_start(message: Message, state: FSMContext):
    if await deny_non_admin(message):
        return
    await state.set_state(EpisodeDelete.command)
    await message.answer(
        "🗑 Qism o'chirish.\n\n"
        "Format:\n"
        "<code>/delep KOD FASL QISM</code>\n\n"
        "Masalan: <code>/delep 123 2 7</code>"
    )


@router.message(Command("delep"))
async def delep_command(message: Message, command: CommandObject, state: FSMContext):
    if await deny_non_admin(message):
        return
    parts = (command.args or "").split()
    if len(parts) != 3:
        await message.answer("❌ Format: <code>/delep KOD FASL QISM</code>")
        return

    code, season_s, number_s = parts
    try:
        season, number = int(season_s), int(number_s)
    except ValueError:
        await message.answer("❌ Fasl va qism raqam bo'lishi kerak.")
        return

    row = await episode_row(code, season, number)
    if not row:
        await message.answer("❌ Qism topilmadi.")
        return

    db.execute("DELETE FROM episodes WHERE id=?", (row["id"],))
    db.commit()
    await state.clear()
    await message.answer(
        f"✅ {escape(row['name'])} — {season}-fasl {number}-qism o'chirildi.",
        reply_markup=admin_keyboard()
    )


# ============================================================
# USER STATISTICS / USERS
# ============================================================
@router.message(F.text == "👥 Foydalanuvchilar")
async def users_button(message: Message):
    if await deny_non_admin(message):
        return

    total = db.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
    day = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    week = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    active_day = db.execute(
        "SELECT COUNT(*) c FROM users WHERE last_seen >= ?", (day,)
    ).fetchone()["c"]
    active_week = db.execute(
        "SELECT COUNT(*) c FROM users WHERE last_seen >= ?", (week,)
    ).fetchone()["c"]

    await message.answer(
        "👥 <b>Foydalanuvchilar</b>\n\n"
        f"Jami foydalanuvchilar: <b>{total}</b>\n"
        f"Oxirgi 24 soatda faol: <b>{active_day}</b>\n"
        f"Oxirgi 7 kunda faol: <b>{active_week}</b>\n\n"
        "ℹ️ Telegram botlar boshqa foydalanuvchilarning haqiqiy "
        "online holatini to'liq bermaydi. Shuning uchun faol foydalanuvchi "
        "oxirgi marta botga murojaat qilgan vaqt bo'yicha hisoblanadi."
    )


@router.message(F.text == "📊 Statistika")
async def statistics_button(message: Message):
    if await deny_non_admin(message):
        return

    anime_count = db.execute(
        "SELECT COUNT(*) c FROM animes"
    ).fetchone()["c"]
    episode_count = db.execute(
        "SELECT COUNT(*) c FROM episodes"
    ).fetchone()["c"]
    user_count = db.execute(
        "SELECT COUNT(*) c FROM users"
    ).fetchone()["c"]
    admin_count = db.execute(
        "SELECT COUNT(*) c FROM admins"
    ).fetchone()["c"]

    top = db.execute("""
        SELECT a.name, a.code, COALESCE(SUM(e.views), 0) views
        FROM animes a
        LEFT JOIN episodes e ON e.anime_id=a.id
        GROUP BY a.id
        ORDER BY views DESC, a.name
        LIMIT 1
    """).fetchone()

    top_text = "Hali ko'rish statistikasi yo'q."
    if top:
        top_text = (
            f"<b>{escape(top['name'])}</b> "
            f"(kod: <code>{escape(top['code'])}</code>) — "
            f"{top['views']} ta ko'rish"
        )

    await message.answer(
        "📊 <b>Umumiy statistika</b>\n\n"
        f"🎬 Anime: <b>{anime_count}</b>\n"
        f"🎞 Qismlar: <b>{episode_count}</b>\n"
        f"👥 Foydalanuvchilar: <b>{user_count}</b>\n"
        f"👑 Adminlar: <b>{admin_count}</b>\n\n"
        f"🔥 <b>Eng ko'p ko'rilgan anime:</b>\n{top_text}"
    )


# ============================================================
# REQUIRED SUBSCRIPTION
# ============================================================
def subscription_admin_keyboard():
    b = InlineKeyboardBuilder()
    b.button(text="➕ Kanal qo'shish", callback_data="sub:add")
    b.button(text="➖ Kanal olish", callback_data="sub:remove")
    b.button(text="📋 Kanallar", callback_data="sub:list")
    b.adjust(1)
    return b.as_markup()


@router.message(F.text == "🔔 Majburiy obuna")
async def subscription_admin(message: Message):
    if await deny_non_admin(message):
        return
    await message.answer(
        "🔔 <b>Majburiy obuna</b>\n\n"
        "Kanal ID yoki @username orqali qo'shish mumkin.",
        reply_markup=subscription_admin_keyboard()
    )


@router.callback_query(F.data == "sub:add")
async def sub_add_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return
    await state.set_state(ChannelAdd.reference)
    await callback.message.answer(
        "➕ Kanal ID yoki @username yuboring.\n\n"
        "Masalan:\n<code>-1001234567890</code>\n"
        "yoki <code>@kanal</code>"
    )
    await callback.answer()


@router.message(ChannelAdd.reference)
async def sub_add_reference(message: Message, state: FSMContext):
    ref = message.text.strip()
    if not ref:
        await message.answer("❌ Kanalni kiriting.")
        return

    try:
        chat = await bot.get_chat(ref)
    except Exception as e:
        await message.answer(
            "❌ Kanal topilmadi yoki bot kanalni ko'ra olmayapti.\n"
            "Botni kanalga admin qilib qo'yganingizni tekshiring."
        )
        log.warning("get_chat failed: %s", e)
        return

    # Telegram requires the bot to be able to inspect membership.
    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(chat.id, me.id)
        if member.status not in {
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR
        }:
            await message.answer(
                "❌ Bot bu kanalda admin emas. Avval botni kanalga admin qiling."
            )
            return
    except Exception:
        await message.answer(
            "⚠️ Botning kanalga admin ekanini tekshirib bo'lmadi. "
            "Botni admin qilib, qayta urinib ko'ring."
        )
        return

    username = chat.username
    default_link = f"https://t.me/{username}" if username else ""
    await state.update_data(
        chat_ref=str(chat.id),
        title=chat.title or str(chat.id),
        default_link=default_link
    )
    await state.set_state(ChannelAdd.link)

    if default_link:
        await message.answer(
            f"📌 Kanal: <b>{escape(chat.title or '')}</b>\n\n"
            f"Linkni yuboring yoki shu linkni tasdiqlash uchun:\n"
            f"<code>{default_link}</code>"
        )
    else:
        await message.answer(
            "🔗 Endi kanalga kirish linkini yuboring.\n"
            "Masalan: <code>https://t.me/+xxxxxxxx</code>"
        )


@router.message(ChannelAdd.link)
async def sub_add_link(message: Message, state: FSMContext):
    data = await state.get_data()
    link = message.text.strip()

    if not link.startswith("https://t.me/"):
        await message.answer("❌ Link https://t.me/ bilan boshlansin.")
        return

    db.execute("""
        INSERT INTO required_channels(chat_ref, title, invite_link, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(chat_ref) DO UPDATE SET
            title=excluded.title,
            invite_link=excluded.invite_link
    """, (
        data["chat_ref"],
        data["title"],
        link,
        now()
    ))
    db.commit()
    await state.clear()

    await message.answer(
        f"✅ <b>{escape(data['title'])}</b> majburiy obunaga qo'shildi.",
        reply_markup=admin_keyboard()
    )


@router.callback_query(F.data == "sub:list")
async def sub_list(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return
    rows = await get_required_channels()
    if not rows:
        await callback.message.answer("📭 Majburiy kanallar yo'q.")
        await callback.answer()
        return

    text = "🔔 <b>Majburiy kanallar</b>\n\n"
    for i, r in enumerate(rows, 1):
        text += (
            f"{i}. {escape(r['title'])}\n"
            f"ID: <code>{escape(r['chat_ref'])}</code>\n"
            f"Link: {escape(r['invite_link'])}\n\n"
        )
    await callback.message.answer(text)
    await callback.answer()


@router.callback_query(F.data == "sub:remove")
async def sub_remove_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return
    await state.set_state(ChannelRemove.target)
    await callback.message.answer(
        "➖ O'chiriladigan kanal ID yoki @username yuboring:"
    )
    await callback.answer()


@router.message(ChannelRemove.target)
async def sub_remove_finish(message: Message, state: FSMContext):
    if await deny_non_admin(message):
        return

    ref = message.text.strip()
    if ref.startswith("@"):
        try:
            chat = await bot.get_chat(ref)
            ref = str(chat.id)
        except Exception:
            await message.answer("❌ Kanal topilmadi.")
            return

    db.execute(
        "DELETE FROM required_channels WHERE chat_ref=?",
        (ref,)
    )
    db.commit()
    await state.clear()
    await message.answer(
        "✅ Kanal majburiy obunadan olindi.",
        reply_markup=admin_keyboard()
    )


# ============================================================
# DIRECT ADMIN COMMANDS
# ============================================================
@router.message(Command("stats"))
async def stats_command(message: Message):
    if await deny_non_admin(message):
        return
    # Reuse the same visible statistics.
    await statistics_button(message)


# ============================================================
# STARTUP
# ============================================================
async def main():
    init_db()
    log.info("Anime Pro Bot started.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Bot stopped.")
