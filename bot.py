import os
import asyncio
import logging
import random
import sqlite3
import json
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Tuple, Any

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ======================== КОНФИГУРАЦИЯ ========================

# Читаем токен из переменной окружения (обязательно!)
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не задан! Укажите его в переменных окружения Railway.")

ADMIN_LOGIN = "rzk1488"
ADMIN_PASSWORD = "rzksigma"
ADMIN_SESSION_TIMEOUT = 15  # минут
SHOOTOUT_SAVE_CHANCE = 0.68  # 68%
MMR_BASE = 1000
MMR_SPREAD = 20

# ======================== ИНИЦИАЛИЗАЦИЯ ========================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ======================== БАЗА ДАННЫХ ========================

DB_NAME = "rpl_bot.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            tg_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS shootout_stats (
            tg_id INTEGER PRIMARY KEY,
            attempts INTEGER DEFAULT 0,
            goals INTEGER DEFAULT 0
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS stick_duel_stats (
            tg_id INTEGER PRIMARY KEY,
            mmr INTEGER DEFAULT 1000,
            games INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS target_chats (
            chat_id INTEGER PRIMARY KEY,
            name TEXT
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS source_channels (
            channel_id INTEGER PRIMARY KEY,
            username TEXT,
            title TEXT
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS support_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER,
            message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            answered BOOLEAN DEFAULT 0,
            answer TEXT,
            answered_at TIMESTAMP
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS admin_sessions (
            tg_id INTEGER PRIMARY KEY,
            login_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS stick_duel_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player1_id INTEGER,
            player2_id INTEGER,
            state TEXT,
            current_broker INTEGER,
            p1_shots TEXT,
            p2_shots TEXT,
            p1_blocks TEXT,
            p2_blocks TEXT,
            p1_score INTEGER DEFAULT 0,
            p2_score INTEGER DEFAULT 0,
            round INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_ai BOOLEAN DEFAULT 0
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS gif_settings (
            key TEXT PRIMARY KEY,
            url TEXT
        )
    ''')
    cur.execute("INSERT OR IGNORE INTO gif_settings (key, url) VALUES ('goal', 'https://example.com/goal.gif')")
    cur.execute("INSERT OR IGNORE INTO gif_settings (key, url) VALUES ('save', 'https://example.com/save.gif')")
    conn.commit()
    conn.close()

init_db()

# ======================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ БД ========================

def get_user(tg_id: int) -> Optional[dict]:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT tg_id, username, first_name, last_name FROM users WHERE tg_id=?", (tg_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        return {"tg_id": row[0], "username": row[1], "first_name": row[2], "last_name": row[3]}
    return None

def register_user(tg_id: int, username: str, first_name: str, last_name: str = ""):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO users (tg_id, username, first_name, last_name) VALUES (?, ?, ?, ?)",
        (tg_id, username or "", first_name or "", last_name or "")
    )
    cur.execute("INSERT OR IGNORE INTO shootout_stats (tg_id) VALUES (?)", (tg_id,))
    cur.execute("INSERT OR IGNORE INTO stick_duel_stats (tg_id) VALUES (?)", (tg_id,))
    conn.commit()
    conn.close()

def get_shootout_stats(tg_id: int) -> Tuple[int, int]:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT attempts, goals FROM shootout_stats WHERE tg_id=?", (tg_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        return row[0], row[1]
    return 0, 0

def update_shootout_stats(tg_id: int, attempts: int, goals: int):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("UPDATE shootout_stats SET attempts=attempts+?, goals=goals+? WHERE tg_id=?", (attempts, goals, tg_id))
    conn.commit()
    conn.close()

def get_stick_duel_stats(tg_id: int) -> Tuple[int, int, int]:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT mmr, games, wins FROM stick_duel_stats WHERE tg_id=?", (tg_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        return row[0], row[1], row[2]
    return 1000, 0, 0

def update_stick_duel_stats(tg_id: int, mmr_delta: int, games_delta: int = 1, wins_delta: int = 0):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "UPDATE stick_duel_stats SET mmr=mmr+?, games=games+?, wins=wins+? WHERE tg_id=?",
        (mmr_delta, games_delta, wins_delta, tg_id)
    )
    conn.commit()
    conn.close()

def reset_mmr(tg_id: int):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("UPDATE stick_duel_stats SET mmr=1000, games=0, wins=0 WHERE tg_id=?", (tg_id,))
    conn.commit()
    conn.close()

def reset_shootout(tg_id: int):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("UPDATE shootout_stats SET attempts=0, goals=0 WHERE tg_id=?", (tg_id,))
    conn.commit()
    conn.close()

def is_admin_logged(tg_id: int) -> bool:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT login_time FROM admin_sessions WHERE tg_id=?", (tg_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        login_time = datetime.fromisoformat(row[0])
        if datetime.now() - login_time < timedelta(minutes=ADMIN_SESSION_TIMEOUT):
            return True
        else:
            conn = sqlite3.connect(DB_NAME)
            cur = conn.cursor()
            cur.execute("DELETE FROM admin_sessions WHERE tg_id=?", (tg_id,))
            conn.commit()
            conn.close()
            return False
    return False

def login_admin(tg_id: int):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO admin_sessions (tg_id, login_time) VALUES (?, ?)",
        (tg_id, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

def logout_admin(tg_id: int):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM admin_sessions WHERE tg_id=?", (tg_id,))
    conn.commit()
    conn.close()

def add_target_chat(chat_id: int, name: str = ""):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO target_chats (chat_id, name) VALUES (?, ?)", (chat_id, name))
    conn.commit()
    conn.close()

def remove_target_chat(chat_id: int):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM target_chats WHERE chat_id=?", (chat_id,))
    conn.commit()
    conn.close()

def get_target_chats() -> List[dict]:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT chat_id, name FROM target_chats")
    rows = cur.fetchall()
    conn.close()
    return [{"chat_id": r[0], "name": r[1]} for r in rows]

def add_source_channel(channel_id: int, username: str, title: str = ""):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO source_channels (channel_id, username, title) VALUES (?, ?, ?)",
                (channel_id, username, title))
    conn.commit()
    conn.close()

def remove_source_channel(channel_id: int):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM source_channels WHERE channel_id=?", (channel_id,))
    conn.commit()
    conn.close()

def get_source_channels() -> List[dict]:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT channel_id, username, title FROM source_channels")
    rows = cur.fetchall()
    conn.close()
    return [{"channel_id": r[0], "username": r[1], "title": r[2]} for r in rows]

def add_support_ticket(tg_id: int, message: str):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("INSERT INTO support_tickets (tg_id, message) VALUES (?, ?)", (tg_id, message))
    conn.commit()
    conn.close()

def get_unanswered_tickets() -> List[dict]:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT id, tg_id, message, created_at FROM support_tickets WHERE answered=0 ORDER BY created_at")
    rows = cur.fetchall()
    conn.close()
    return [{"id": r[0], "tg_id": r[1], "message": r[2], "created_at": r[3]} for r in rows]

def get_ticket(ticket_id: int) -> Optional[dict]:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT id, tg_id, message, created_at, answered, answer FROM support_tickets WHERE id=?", (ticket_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        return {"id": row[0], "tg_id": row[1], "message": row[2], "created_at": row[3], "answered": row[4], "answer": row[5]}
    return None

def answer_ticket(ticket_id: int, answer: str):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "UPDATE support_tickets SET answered=1, answer=?, answered_at=? WHERE id=?",
        (answer, datetime.now().isoformat(), ticket_id)
    )
    conn.commit()
    conn.close()

def get_gif_url(key: str) -> str:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT url FROM gif_settings WHERE key=?", (key,))
    row = cur.fetchone()
    conn.close()
    if row:
        return row[0]
    return None

def set_gif_url(key: str, url: str):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("UPDATE gif_settings SET url=? WHERE key=?", (url, key))
    conn.commit()
    conn.close()

def create_match(player1: int, player2: int = None, is_ai: bool = False) -> int:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    state = "playing" if player2 else "waiting"
    cur.execute(
        "INSERT INTO stick_duel_matches (player1_id, player2_id, state, current_broker, p1_shots, p2_shots, p1_blocks, p2_blocks, is_ai) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (player1, player2 or 0, state, player1, "[]", "[]", "[]", "[]", 1 if is_ai else 0)
    )
    match_id = cur.lastrowid
    conn.commit()
    conn.close()
    return match_id

def get_match(match_id: int) -> Optional[dict]:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT * FROM stick_duel_matches WHERE id=?", (match_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        columns = ["id", "player1_id", "player2_id", "state", "current_broker", 
                   "p1_shots", "p2_shots", "p1_blocks", "p2_blocks", 
                   "p1_score", "p2_score", "round", "created_at", "is_ai"]
        return dict(zip(columns, row))
    return None

def update_match(match_id: int, **kwargs):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    set_clause = ", ".join([f"{k}=?" for k in kwargs])
    values = list(kwargs.values()) + [match_id]
    cur.execute(f"UPDATE stick_duel_matches SET {set_clause} WHERE id=?", values)
    conn.commit()
    conn.close()

def get_active_match_for_player(tg_id: int) -> Optional[dict]:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM stick_duel_matches WHERE (player1_id=? OR player2_id=?) AND state IN ('waiting','playing')",
        (tg_id, tg_id)
    )
    row = cur.fetchone()
    conn.close()
    if row:
        columns = ["id", "player1_id", "player2_id", "state", "current_broker", 
                   "p1_shots", "p2_shots", "p1_blocks", "p2_blocks", 
                   "p1_score", "p2_score", "round", "created_at", "is_ai"]
        return dict(zip(columns, row))
    return None

# ======================== КЛАВИАТУРЫ ========================

def main_menu_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🏒 Дуэль буллитов", callback_data="shootout")
    builder.button(text="🏑 Дуэль Клюшек", callback_data="stickduel")
    builder.button(text="👤 Профиль", callback_data="profile")
    builder.button(text="💬 Наш Discord", url="https://discord.gg/wqPSk3dCuw")
    builder.button(text="🌐 Наш Сайт", url="https://rplpuck.ru")
    builder.button(text="📩 Поддержка", callback_data="support")
    builder.adjust(2, 1, 2, 1)
    return builder.as_markup()

def shot_keyboard(prefix="shot"):
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Левая Девятка", callback_data=f"{prefix}_left")
    builder.button(text="➡️ Правая Девятка", callback_data=f"{prefix}_right")
    builder.button(text="🏠 Домик", callback_data=f"{prefix}_house")
    builder.adjust(2, 1)
    return builder.as_markup()

def block_keyboard(prefix="block"):
    builder = InlineKeyboardBuilder()
    builder.button(text="🧤 Девятки", callback_data=f"{prefix}_corners")
    builder.button(text="🏠 Домик", callback_data=f"{prefix}_house")
    builder.adjust(2)
    return builder.as_markup()

def admin_main_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить чаты", callback_data="admin_add_chat")
    builder.button(text="➕ Добавить каналы", callback_data="admin_add_channel")
    builder.button(text="⚙️ Настройки", callback_data="admin_settings")
    builder.button(text="🎮 Настройки игры", callback_data="admin_game_settings")
    builder.button(text="📩 Проверить поддержку", callback_data="admin_support")
    builder.button(text="🚪 Выход", callback_data="admin_logout")
    builder.adjust(2)
    return builder.as_markup()

def back_button(callback_data="admin_back"):
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад", callback_data=callback_data)
    return builder.as_markup()

# ======================== СОСТОЯНИЯ FSM ========================

class AdminStates(StatesGroup):
    login = State()
    main = State()
    add_chat = State()
    remove_chat = State()
    add_channel = State()
    remove_channel = State()
    support_view = State()
    support_answer = State()
    reset_confirm = State()
    gif_goal = State()
    gif_save = State()

class SupportStates(StatesGroup):
    waiting_message = State()

class StickDuelStates(StatesGroup):
    waiting_opponent = State()
    playing = State()

# ======================== ОБРАБОТЧИКИ КОМАНД ========================

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user = message.from_user
    register_user(user.id, user.username or "", user.first_name or "", user.last_name or "")
    await message.answer(
        "🏒 *Добро пожаловать в Russian Puck League Bot!* 🏒\n\n"
        "Выберите раздел:",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown"
    )

@dp.message(Command("duelrpl"))
async def cmd_duelrpl(message: types.Message):
    await message.answer(
        "🎯 *Дуэль буллитов*\nВыберите куда бросить:",
        reply_markup=shot_keyboard("shootout"),
        parse_mode="Markdown"
    )

@dp.message(Command("regrpl"))
async def cmd_regrpl(message: types.Message):
    tg_id = message.from_user.id
    register_user(tg_id, message.from_user.username or "", message.from_user.first_name or "")
    match = get_active_match_for_player(tg_id)
    if match:
        await message.answer("❌ Вы уже в активной игре или поиске.")
        return
    match_id = create_match(tg_id)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Принять", callback_data=f"stick_accept_{match_id}")],
        [InlineKeyboardButton(text="🤖 Игра с ИИ", callback_data=f"stick_ai_{match_id}")],
        [InlineKeyboardButton(text="❌ Отменить", callback_data=f"stick_cancel_{match_id}")]
    ])
    await message.answer(
        "🔍 *Поиск противника...*\n"
        "Ожидайте, пока кто-то примет игру.\n"
        "Или нажмите 'Игра с ИИ', чтобы сыграть с ботом.",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

@dp.message(Command("rating"))
async def cmd_rating(message: types.Message):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute('''
        SELECT u.tg_id, u.first_name, u.last_name, u.username,
               s.attempts, s.goals
        FROM users u
        JOIN shootout_stats s ON u.tg_id = s.tg_id
        WHERE s.attempts > 4
        ORDER BY (s.goals * 1.0 / s.attempts) DESC
        LIMIT 10
    ''')
    rows = cur.fetchall()
    conn.close()
    if not rows:
        await message.answer("📊 Нет игроков с более чем 4 бросками.")
        return
    text = "🏆 *Рейтинг Дуэли Буллитов*\n\n"
    medals = ["🥇", "🥈", "🥉"]
    for i, row in enumerate(rows):
        tg_id, first_name, last_name, username, attempts, goals = row
        name = f"{first_name or ''} {last_name or ''}".strip() or username or str(tg_id)
        percent = (goals / attempts * 100) if attempts > 0 else 0
        medal = medals[i] if i < 3 else f"{i+1}."
        text += f"{medal} {name}: {goals}/{attempts} ({percent:.1f}%)\n"
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("ratingmmr"))
async def cmd_ratingmmr(message: types.Message):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute('''
        SELECT u.tg_id, u.first_name, u.last_name, u.username,
               s.mmr, s.games, s.wins
        FROM users u
        JOIN stick_duel_stats s ON u.tg_id = s.tg_id
        WHERE s.games > 0
        ORDER BY s.mmr DESC
        LIMIT 10
    ''')
    rows = cur.fetchall()
    conn.close()
    if not rows:
        await message.answer("📊 Нет игроков с сыгранными матчами в Дуэли Клюшек.")
        return
    text = "🏆 *Рейтинг MMR Дуэли Клюшек*\n\n"
    medals = ["🥇", "🥈", "🥉"]
    for i, row in enumerate(rows):
        tg_id, first_name, last_name, username, mmr, games, wins = row
        name = f"{first_name or ''} {last_name or ''}".strip() or username or str(tg_id)
        medal = medals[i] if i < 3 else f"{i+1}."
        text += f"{medal} {name}: MMR {mmr} (игр: {games}, побед: {wins})\n"
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("getid"))
async def cmd_getid(message: types.Message):
    if message.chat.type in ["group", "supergroup"]:
        await message.answer(f"🆔 ID этого чата: `{message.chat.id}`", parse_mode="Markdown")
    else:
        await message.answer("❌ Эта команда работает только в группах.")

# ======================== ОБРАБОТЧИКИ CALLBACK (ГЛАВНОЕ МЕНЮ) ========================

@dp.callback_query(F.data == "shootout")
async def cb_shootout(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "🎯 *Дуэль буллитов*\nВыберите куда бросить:",
        reply_markup=shot_keyboard("shootout"),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "stickduel")
async def cb_stickduel(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "🏑 *Дуэль Клюшек*\n\nДля начала игры используйте команду /regrpl",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "profile")
async def cb_profile(callback: CallbackQuery):
    await callback.answer()
    tg_id = callback.from_user.id
    user = get_user(tg_id)
    if not user:
        await callback.message.edit_text("❌ Пользователь не найден.")
        return
    name = f"{user['first_name']} {user['last_name'] or ''}".strip() or user['username'] or "Без имени"
    shoot_attempts, shoot_goals = get_shootout_stats(tg_id)
    mmr, games, wins = get_stick_duel_stats(tg_id)
    text = f"👤 *Профиль*\n\n"
    text += f"Имя: {name}\n"
    text += f"ID: `{tg_id}`\n"
    if shoot_attempts > 4:
        percent = (shoot_goals / shoot_attempts * 100) if shoot_attempts > 0 else 0
        text += f"🏒 Рейтинг Дуэли Буллитов: {shoot_goals}/{shoot_attempts} ({percent:.1f}%)\n"
    else:
        text += "🏒 Рейтинг Дуэли Буллитов: нет данных (нужно >4 бросков)\n"
    if games > 0:
        text += f"🏑 MMR Дуэли Клюшек: {mmr}\n"
        text += f"Игр: {games}, Побед: {wins}\n"
    else:
        text += "🏑 MMR Дуэли Клюшек: нет игр\n"
    await callback.message.edit_text(text, reply_markup=main_menu_keyboard(), parse_mode="Markdown")

@dp.callback_query(F.data == "support")
async def cb_support(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text(
        "📩 *Поддержка*\n\nНапишите ваше сообщение (максимум 500 символов):",
        parse_mode="Markdown"
    )
    await state.set_state(SupportStates.waiting_message)

@dp.message(SupportStates.waiting_message)
async def support_message(message: types.Message, state: FSMContext):
    if len(message.text) > 500:
        await message.answer("❌ Сообщение слишком длинное. Максимум 500 символов.")
        return
    add_support_ticket(message.from_user.id, message.text)
    await message.answer("✅ Ваше сообщение отправлено в поддержку. Мы ответим вам в ближайшее время.")
    await state.clear()

# ======================== ДУЭЛЬ БУЛЛИТОВ ========================

@dp.callback_query(F.data.startswith("shootout_"))
async def shootout_shot(callback: CallbackQuery):
    await callback.answer()
    tg_id = callback.from_user.id
    shot = callback.data.split("_")[1]
    is_saved = random.random() < SHOOTOUT_SAVE_CHANCE
    if is_saved:
        update_shootout_stats(tg_id, 1, 0)
        gif_url = get_gif_url("save")
        result_text = "🧤 *Вратарь отбил!*"
    else:
        update_shootout_stats(tg_id, 1, 1)
        gif_url = get_gif_url("goal")
        result_text = "⚡ *ГОЛ!* 🎉"
    if gif_url:
        await callback.message.answer_animation(gif_url, caption=result_text, parse_mode="Markdown")
    else:
        await callback.message.answer(result_text, parse_mode="Markdown")
    await callback.message.answer(
        "🎯 Продолжить игру: /duelrpl\nВернуться в меню: /start",
        reply_markup=main_menu_keyboard()
    )
    await callback.message.delete()

# ======================== ДУЭЛЬ КЛЮШЕК ========================

@dp.callback_query(F.data.startswith("stick_accept_"))
async def stick_accept(callback: CallbackQuery):
    await callback.answer()
    tg_id = callback.from_user.id
    match_id = int(callback.data.split("_")[2])
    match = get_match(match_id)
    if not match:
        await callback.message.edit_text("❌ Игра уже не существует.")
        return
    if match["state"] != "waiting":
        await callback.message.edit_text("❌ Игра уже начата или завершена.")
        return
    if match["player1_id"] == tg_id:
        await callback.message.edit_text("❌ Нельзя принять самого себя.")
        return
    if get_active_match_for_player(tg_id):
        await callback.message.edit_text("❌ Вы уже в другой игре.")
        return
    update_match(match_id, player2_id=tg_id, state="playing", current_broker=match["player1_id"], round=0)
    p1 = match["player1_id"]
    await bot.send_message(p1, f"✅ Противник найден! Начинается матч.\nВы первый бросаете.")
    await bot.send_message(tg_id, f"✅ Вы приняли игру. Ожидайте ход соперника.")
    await bot.send_message(p1, "🎯 Ваш ход! Выберите бросок:", reply_markup=shot_keyboard("stick_shot"))
    try:
        await bot.delete_message(p1, callback.message.message_id)
    except:
        pass
    await callback.message.edit_text("✅ Игра начата! Ожидайте ход.")

@dp.callback_query(F.data.startswith("stick_ai_"))
async def stick_ai(callback: CallbackQuery):
    await callback.answer()
    tg_id = callback.from_user.id
    match_id = int(callback.data.split("_")[2])
    match = get_match(match_id)
    if not match or match["player1_id"] != tg_id:
        await callback.message.edit_text("❌ Ошибка.")
        return
    if match["state"] != "waiting":
        await callback.message.edit_text("❌ Игра уже начата.")
        return
    update_match(match_id, state="playing", current_broker=tg_id, is_ai=1, round=0)
    await callback.message.edit_text("🤖 Игра с ИИ начата! Вы бросаете первым.")
    await bot.send_message(tg_id, "🎯 Ваш ход! Выберите бросок:", reply_markup=shot_keyboard("stick_shot"))

@dp.callback_query(F.data.startswith("stick_cancel_"))
async def stick_cancel(callback: CallbackQuery):
    await callback.answer()
    tg_id = callback.from_user.id
    match_id = int(callback.data.split("_")[2])
    match = get_match(match_id)
    if not match or match["player1_id"] != tg_id:
        await callback.message.edit_text("❌ Ошибка.")
        return
    if match["state"] != "waiting":
        await callback.message.edit_text("❌ Игру уже нельзя отменить.")
        return
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM stick_duel_matches WHERE id=?", (match_id,))
    conn.commit()
    conn.close()
    await callback.message.edit_text("❌ Поиск отменен.", reply_markup=main_menu_keyboard())

@dp.callback_query(F.data.startswith("stick_shot_"))
async def stick_shot(callback: CallbackQuery):
    await callback.answer()
    tg_id = callback.from_user.id
    shot = callback.data.split("_")[2]
    match = get_active_match_for_player(tg_id)
    if not match:
        await callback.message.edit_text("❌ Вы не в игре.")
        return
    if match["state"] != "playing":
        await callback.message.edit_text("❌ Игра уже завершена.")
        return
    if match["current_broker"] != tg_id:
        await callback.message.edit_text("❌ Сейчас не ваш ход.")
        return
    p1 = match["player1_id"]
    p2 = match["player2_id"]
    is_ai = match["is_ai"]
    attacker = tg_id
    defender = p2 if tg_id == p1 else p1
    current_round = match["round"]
    if current_round >= 6:
        await callback.message.edit_text("❌ Игра уже завершена.")
        return
    p1_shots = json.loads(match["p1_shots"])
    p2_shots = json.loads(match["p2_shots"])
    if tg_id == p1:
        p1_shots.append(shot)
        update_match(match["id"], p1_shots=json.dumps(p1_shots))
    else:
        p2_shots.append(shot)
        update_match(match["id"], p2_shots=json.dumps(p2_shots))
    if is_ai and defender == 0:
        block = random.choice(["corners", "house"])
        await process_block(match["id"], defender, block, attacker, callback)
        return
    else:
        await bot.send_message(defender, f"🛡️ Противник сделал бросок! Выберите защиту:",
                               reply_markup=block_keyboard(f"stick_block_{match['id']}"))
        await callback.message.edit_text("✅ Ваш бросок записан. Ожидайте решение защитника.")
        update_match(match["id"], current_broker=defender)

@dp.callback_query(F.data.startswith("stick_block_"))
async def stick_block(callback: CallbackQuery):
    await callback.answer()
    tg_id = callback.from_user.id
    parts = callback.data.split("_")
    match_id = int(parts[2])
    block = parts[3]
    match = get_match(match_id)
    if not match:
        await callback.message.edit_text("❌ Игра не найдена.")
        return
    if match["state"] != "playing":
        await callback.message.edit_text("❌ Игра уже завершена.")
        return
    if match["current_broker"] != tg_id:
        await callback.message.edit_text("❌ Сейчас не ваш ход.")
        return
    p1 = match["player1_id"]
    p2 = match["player2_id"]
    defender = tg_id
    attacker = p2 if tg_id == p1 else p1
    await process_block(match_id, defender, block, attacker, callback)

async def process_block(match_id: int, defender_id: int, block: str, attacker_id: int, callback: CallbackQuery = None):
    match = get_match(match_id)
    if not match:
        return
    p1_shots = json.loads(match["p1_shots"])
    p2_shots = json.loads(match["p2_shots"])
    if attacker_id == match["player1_id"]:
        shot = p1_shots[-1] if p1_shots else None
        p2_blocks = json.loads(match["p2_blocks"])
        p2_blocks.append(block)
        update_match(match_id, p2_blocks=json.dumps(p2_blocks))
    else:
        shot = p2_shots[-1] if p2_shots else None
        p1_blocks = json.loads(match["p1_blocks"])
        p1_blocks.append(block)
        update_match(match_id, p1_blocks=json.dumps(p1_blocks))
    if not shot:
        return
    is_saved = False
    if shot in ["left", "right"] and block == "corners":
        is_saved = True
    elif shot == "house" and block == "house":
        is_saved = True
    p1_score = match["p1_score"]
    p2_score = match["p2_score"]
    if not is_saved:
        if attacker_id == match["player1_id"]:
            p1_score += 1
        else:
            p2_score += 1
    new_round = match["round"] + 1
    finished = new_round >= 6
    if finished:
        winner = None
        if p1_score > p2_score:
            winner = match["player1_id"]
        elif p2_score > p1_score:
            winner = match["player2_id"]
        if winner:
            p1_mmr, _, _ = get_stick_duel_stats(match["player1_id"])
            p2_mmr, _, _ = get_stick_duel_stats(match["player2_id"]) if match["player2_id"] else (1000,0,0)
            delta = 15
            if winner == match["player1_id"]:
                diff = p1_mmr - p2_mmr
            else:
                diff = p2_mmr - p1_mmr
            delta = max(10, 15 - diff // 20)
            if winner == match["player1_id"]:
                update_stick_duel_stats(match["player1_id"], delta, 1, 1)
                if match["player2_id"]:
                    update_stick_duel_stats(match["player2_id"], -delta, 1, 0)
            else:
                update_stick_duel_stats(match["player2_id"], delta, 1, 1)
                if match["player1_id"]:
                    update_stick_duel_stats(match["player1_id"], -delta, 1, 0)
        else:
            update_stick_duel_stats(match["player1_id"], 0, 1, 0)
            if match["player2_id"]:
                update_stick_duel_stats(match["player2_id"], 0, 1, 0)
        update_match(match_id, state="finished", p1_score=p1_score, p2_score=p2_score, round=new_round)
        result_text = f"🏁 *Матч завершен!*\n\n"
        result_text += f"Игрок1: {p1_score} голов\n"
        if match["player2_id"]:
            result_text += f"Игрок2: {p2_score} голов\n"
        else:
            result_text += f"ИИ: {p2_score} голов\n"
        if winner:
            result_text += f"🏆 Победитель: {winner}"
        else:
            result_text += "🤝 Ничья!"
        await bot.send_message(match["player1_id"], result_text, parse_mode="Markdown")
        if match["player2_id"]:
            await bot.send_message(match["player2_id"], result_text, parse_mode="Markdown")
        if callback:
            await callback.message.edit_text("✅ Матч завершен.")
        return
    else:
        update_match(match_id, p1_score=p1_score, p2_score=p2_score, round=new_round,
                     current_broker=attacker_id)
        await bot.send_message(defender_id, f"✅ Ваш блок засчитан.")
        await bot.send_message(attacker_id, "🎯 Теперь ваш ход! Выберите бросок:", reply_markup=shot_keyboard("stick_shot"))
        if callback:
            await callback.message.edit_text("✅ Ваш блок засчитан. Ожидайте ход соперника.")

# ======================== АДМИНКА ========================

@dp.message(Command("adminkarpl"))
async def cmd_adminkarpl(message: types.Message):
    tg_id = message.from_user.id
    if is_admin_logged(tg_id):
        await message.answer("🔐 Вы уже авторизованы. Меню админа:", reply_markup=admin_main_keyboard())
        return
    await message.answer("🔑 Введите логин и пароль через пробел (логин пароль):")
    await AdminStates.login.set()

@dp.message(AdminStates.login)
async def admin_login_process(message: types.Message, state: FSMContext):
    parts = message.text.strip().split()
    if len(parts) != 2:
        await message.answer("❌ Неверный формат. Введите логин и пароль через пробел.")
        return
    login, password = parts
    if login == ADMIN_LOGIN and password == ADMIN_PASSWORD:
        login_admin(message.from_user.id)
        await message.answer("✅ Авторизация успешна. Меню админа:", reply_markup=admin_main_keyboard())
        await state.clear()
    else:
        await message.answer("❌ Неверный логин или пароль. Попробуйте снова.")

@dp.callback_query(F.data == "admin_logout")
async def admin_logout(callback: CallbackQuery):
    await callback.answer()
    tg_id = callback.from_user.id
    logout_admin(tg_id)
    await callback.message.edit_text("🚪 Вы вышли из админки. Для входа используйте /adminkarpl")

@dp.callback_query(F.data == "admin_add_chat")
async def admin_add_chat(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not is_admin_logged(callback.from_user.id):
        await callback.message.edit_text("❌ Сессия истекла. Войдите заново.")
        return
    await callback.message.edit_text("➕ Введите ID чата (целое число):", reply_markup=back_button("admin_back"))
    await state.set_state(AdminStates.add_chat)

@dp.message(AdminStates.add_chat)
async def admin_add_chat_process(message: types.Message, state: FSMContext):
    if not is_admin_logged(message.from_user.id):
        await message.answer("❌ Сессия истекла.")
        await state.clear()
        return
    try:
        chat_id = int(message.text.strip())
        add_target_chat(chat_id)
        await message.answer(f"✅ Чат {chat_id} добавлен.", reply_markup=admin_main_keyboard())
    except ValueError:
        await message.answer("❌ Неверный ID. Введите целое число.")
        return
    await state.clear()

@dp.callback_query(F.data == "admin_add_channel")
async def admin_add_channel(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not is_admin_logged(callback.from_user.id):
        await callback.message.edit_text("❌ Сессия истекла.")
        return
    await callback.message.edit_text("➕ Введите @username канала (например, @channel):", reply_markup=back_button("admin_back"))
    await state.set_state(AdminStates.add_channel)

@dp.message(AdminStates.add_channel)
async def admin_add_channel_process(message: types.Message, state: FSMContext):
    if not is_admin_logged(message.from_user.id):
        await message.answer("❌ Сессия истекла.")
        await state.clear()
        return
    username = message.text.strip()
    if not username.startswith("@"):
        username = "@" + username
    try:
        chat = await bot.get_chat(username)
        if chat.type != "channel":
            await message.answer("❌ Это не канал.")
            return
        add_source_channel(chat.id, username, chat.title or "")
        await message.answer(f"✅ Канал {username} (ID: {chat.id}) добавлен.", reply_markup=admin_main_keyboard())
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
    await state.clear()

@dp.callback_query(F.data == "admin_settings")
async def admin_settings(callback: CallbackQuery):
    await callback.answer()
    if not is_admin_logged(callback.from_user.id):
        await callback.message.edit_text("❌ Сессия истекла.")
        return
    chats = get_target_chats()
    channels = get_source_channels()
    text = "⚙️ *Настройки*\n\n"
    text += "📢 *Привязанные чаты:*\n"
    if chats:
        for c in chats:
            text += f"• {c['chat_id']} ({c['name'] or 'без имени'})\n"
    else:
        text += "• нет\n"
    text += "\n📣 *Привязанные каналы:*\n"
    if channels:
        for c in channels:
            text += f"• {c['username']} ({c['title'] or 'без названия'})\n"
    else:
        text += "• нет\n"
    builder = InlineKeyboardBuilder()
    for c in chats:
        builder.button(text=f"❌ Удалить чат {c['chat_id']}", callback_data=f"admin_remove_chat_{c['chat_id']}")
    for c in channels:
        builder.button(text=f"❌ Удалить канал {c['username']}", callback_data=f"admin_remove_channel_{c['channel_id']}")
    builder.button(text="🔙 Назад", callback_data="admin_back")
    builder.adjust(1)
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("admin_remove_chat_"))
async def admin_remove_chat(callback: CallbackQuery):
    await callback.answer()
    chat_id = int(callback.data.split("_")[3])
    remove_target_chat(chat_id)
    await callback.message.edit_text(f"✅ Чат {chat_id} удален.", reply_markup=admin_main_keyboard())

@dp.callback_query(F.data.startswith("admin_remove_channel_"))
async def admin_remove_channel(callback: CallbackQuery):
    await callback.answer()
    channel_id = int(callback.data.split("_")[3])
    remove_source_channel(channel_id)
    await callback.message.edit_text(f"✅ Канал удален.", reply_markup=admin_main_keyboard())

@dp.callback_query(F.data == "admin_game_settings")
async def admin_game_settings(callback: CallbackQuery):
    await callback.answer()
    if not is_admin_logged(callback.from_user.id):
        await callback.message.edit_text("❌ Сессия истекла.")
        return
    builder = InlineKeyboardBuilder()
    builder.button(text="🏑 Сбросить MMR Дуэли Клюшек", callback_data="admin_reset_mmr")
    builder.button(text="🏒 Сбросить Рейтинг Дуэли Буллитов", callback_data="admin_reset_shootout")
    builder.button(text="🎥 Настроить GIF (гол)", callback_data="admin_gif_goal")
    builder.button(text="🎥 Настроить GIF (сейв)", callback_data="admin_gif_save")
    builder.button(text="🔙 Назад", callback_data="admin_back")
    builder.adjust(1)
    await callback.message.edit_text("🎮 *Настройки игры*\nВыберите действие:", reply_markup=builder.as_markup(), parse_mode="Markdown")

@dp.callback_query(F.data == "admin_reset_mmr")
async def admin_reset_mmr(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not is_admin_logged(callback.from_user.id):
        await callback.message.edit_text("❌ Сессия истекла.")
        return
    await callback.message.edit_text("⚠️ Введите ID пользователя, которому нужно обнулить MMR:\n(или 'все' для обнуления всех)")
    await state.set_state(AdminStates.reset_confirm)
    await state.update_data(reset_type="mmr")

@dp.callback_query(F.data == "admin_reset_shootout")
async def admin_reset_shootout(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not is_admin_logged(callback.from_user.id):
        await callback.message.edit_text("❌ Сессия истекла.")
        return
    await callback.message.edit_text("⚠️ Введите ID пользователя, которому нужно обнулить рейтинг буллитов:\n(или 'все' для обнуления всех)")
    await state.set_state(AdminStates.reset_confirm)
    await state.update_data(reset_type="shootout")

@dp.message(AdminStates.reset_confirm)
async def admin_reset_process(message: types.Message, state: FSMContext):
    if not is_admin_logged(message.from_user.id):
        await message.answer("❌ Сессия истекла.")
        await state.clear()
        return
    data = await state.get_data()
    reset_type = data.get("reset_type")
    target = message.text.strip()
    if target.lower() == "все":
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        if reset_type == "mmr":
            cur.execute("UPDATE stick_duel_stats SET mmr=1000, games=0, wins=0")
        else:
            cur.execute("UPDATE shootout_stats SET attempts=0, goals=0")
        conn.commit()
        conn.close()
        await message.answer("✅ Все сброшены.", reply_markup=admin_main_keyboard())
    else:
        try:
            tg_id = int(target)
            if reset_type == "mmr":
                reset_mmr(tg_id)
            else:
                reset_shootout(tg_id)
            await message.answer(f"✅ Пользователь {tg_id} сброшен.", reply_markup=admin_main_keyboard())
        except ValueError:
            await message.answer("❌ Неверный ID.")
            return
    await state.clear()

@dp.callback_query(F.data == "admin_gif_goal")
async def admin_gif_goal(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not is_admin_logged(callback.from_user.id):
        await callback.message.edit_text("❌ Сессия истекла.")
        return
    await callback.message.edit_text("🎥 Введите ссылку на GIF для гола (или 'удалить' для сброса):")
    await state.set_state(AdminStates.gif_goal)

@dp.callback_query(F.data == "admin_gif_save")
async def admin_gif_save(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not is_admin_logged(callback.from_user.id):
        await callback.message.edit_text("❌ Сессия истекла.")
        return
    await callback.message.edit_text("🎥 Введите ссылку на GIF для сейва (или 'удалить' для сброса):")
    await state.set_state(AdminStates.gif_save)

@dp.message(AdminStates.gif_goal)
async def admin_gif_goal_process(message: types.Message, state: FSMContext):
    if not is_admin_logged(message.from_user.id):
        await message.answer("❌ Сессия истекла.")
        await state.clear()
        return
    url = message.text.strip()
    if url.lower() == "удалить":
        set_gif_url("goal", "")
        await message.answer("✅ GIF для гола удален.", reply_markup=admin_main_keyboard())
    else:
        set_gif_url("goal", url)
        await message.answer("✅ GIF для гола обновлен.", reply_markup=admin_main_keyboard())
    await state.clear()

@dp.message(AdminStates.gif_save)
async def admin_gif_save_process(message: types.Message, state: FSMContext):
    if not is_admin_logged(message.from_user.id):
        await message.answer("❌ Сессия истекла.")
        await state.clear()
        return
    url = message.text.strip()
    if url.lower() == "удалить":
        set_gif_url("save", "")
        await message.answer("✅ GIF для сейва удален.", reply_markup=admin_main_keyboard())
    else:
        set_gif_url("save", url)
        await message.answer("✅ GIF для сейва обновлен.", reply_markup=admin_main_keyboard())
    await state.clear()

@dp.callback_query(F.data == "admin_support")
async def admin_support(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not is_admin_logged(callback.from_user.id):
        await callback.message.edit_text("❌ Сессия истекла.")
        return
    tickets = get_unanswered_tickets()
    if not tickets:
        await callback.message.edit_text("📭 Нет новых обращений.", reply_markup=admin_main_keyboard())
        return
    ticket = tickets[0]
    text = f"📩 *Обращение #{ticket['id']}*\n"
    text += f"От: {ticket['tg_id']}\n"
    text += f"Дата: {ticket['created_at']}\n"
    text += f"Сообщение:\n{ticket['message']}"
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Ответить", callback_data=f"admin_answer_{ticket['id']}")
    builder.button(text="⏭️ Пропустить", callback_data="admin_support_skip")
    builder.button(text="🔙 Назад", callback_data="admin_back")
    builder.adjust(2, 1)
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")
    await AdminStates.support_view.set()
    await state.update_data(ticket_id=ticket['id'])

@dp.callback_query(F.data.startswith("admin_answer_"))
async def admin_answer(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    ticket_id = int(callback.data.split("_")[2])
    await state.update_data(ticket_id=ticket_id)
    await callback.message.edit_text("✏️ Введите ответ на обращение:")
    await AdminStates.support_answer.set()

@dp.message(AdminStates.support_answer)
async def admin_answer_process(message: types.Message, state: FSMContext):
    if not is_admin_logged(message.from_user.id):
        await message.answer("❌ Сессия истекла.")
        await state.clear()
        return
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    if not ticket_id:
        await message.answer("❌ Ошибка.")
        return
    answer = message.text.strip()
    answer_ticket(ticket_id, answer)
    ticket = get_ticket(ticket_id)
    if ticket:
        try:
            await bot.send_message(ticket['tg_id'], f"📩 Ответ поддержки:\n{answer}")
        except:
            pass
    await message.answer("✅ Ответ отправлен.", reply_markup=admin_main_keyboard())
    await state.clear()

@dp.callback_query(F.data == "admin_support_skip")
async def admin_support_skip(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await admin_support(callback, state)

@dp.callback_query(F.data == "admin_back")
async def admin_back(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await callback.message.edit_text("🔐 Меню админа:", reply_markup=admin_main_keyboard())

# ======================== ПЕРЕСЫЛКА СООБЩЕНИЙ ИЗ КАНАЛОВ ========================

@dp.channel_post()
async def channel_post_handler(message: types.Message):
    if not message.text:
        return
    text = message.text
    required_hashtags = ["#rplpuck", "#MatchDay", "#Result"]
    if all(ht in text for ht in required_hashtags):
        chats = get_target_chats()
        for chat in chats:
            try:
                await bot.forward_message(chat["chat_id"], message.chat.id, message.message_id)
            except Exception as e:
                logger.error(f"Ошибка пересылки в чат {chat['chat_id']}: {e}")

# ======================== ЗАПУСК ========================

async def main():
    logger.info("🚀 Запуск бота...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
