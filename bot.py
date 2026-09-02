import asyncio
import io
import logging
import secrets
import sqlite3

import qrcode
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    Message,
    BufferedInputFile,
    BotCommand,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)

from config import BOT_TOKEN, ADMIN_IDS, DB_PATH
from panel import create_client_link, delete_client

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# ==================== БАЗА ====================

def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            vless_link TEXT NOT NULL
        )
        """
    )
    try:
        conn.execute("ALTER TABLE users ADD COLUMN email TEXT NOT NULL DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS invites (
            token TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            vless_link TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def get_user(telegram_id: int):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT name, email, vless_link FROM users WHERE telegram_id = ?",
        (telegram_id,),
    ).fetchone()
    conn.close()
    return row


def add_user(telegram_id: int, name: str, email: str, vless_link: str) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO users (telegram_id, name, email, vless_link) VALUES (?, ?, ?, ?)",
        (telegram_id, name, email, vless_link),
    )
    conn.commit()
    conn.close()


def remove_user(telegram_id: int) -> str | None:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT email FROM users WHERE telegram_id = ?", (telegram_id,)
    ).fetchone()
    if not row:
        conn.close()
        return None
    conn.execute("DELETE FROM users WHERE telegram_id = ?", (telegram_id,))
    conn.commit()
    conn.close()
    return row[0]


def add_invite(token: str, name: str, email: str, vless_link: str) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO invites (token, name, email, vless_link) VALUES (?, ?, ?, ?)",
        (token, name, email, vless_link),
    )
    conn.commit()
    conn.close()


def pop_invite(token: str):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT name, email, vless_link FROM invites WHERE token = ?",
        (token,),
    ).fetchone()
    if row:
        conn.execute("DELETE FROM invites WHERE token = ?", (token,))
        conn.commit()
    conn.close()
    return row


def list_users():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT telegram_id, name FROM users").fetchall()
    conn.close()
    return rows


def is_admin(telegram_id: int) -> bool:
    return telegram_id in ADMIN_IDS


def make_qr(data: str) -> BufferedInputFile:
    img = qrcode.make(data)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return BufferedInputFile(buf.read(), filename="vless_qr.png")


async def set_bot_commands() -> None:
    await bot.set_my_commands([
        BotCommand(command="mylink", description="🔗 Моя ссылка на VPN"),
        BotCommand(command="addme", description="👤 Выдать доступ себе"),
        BotCommand(command="adduser", description="➕ Пригласить друга"),
        BotCommand(command="removeuser", description="🗑 Удалить пользователя"),
        BotCommand(command="listusers", description="📋 Список пользователей"),
        BotCommand(command="start", description="ℹ️ О боте"),
    ])


# ==================== /start ====================

@dp.message(Command("start"))
async def cmd_start(message: Message, command: CommandObject):
    # 1) Приглашение другу: /start <token>
    if command.args:
        token = command.args.strip()
        invite = pop_invite(token)
        if not invite:
            await message.answer("Ссылка-приглашение недействительна или уже использована.")
            return
        name, email, vless_link = invite
        telegram_id = message.from_user.id
        if get_user(telegram_id):
            await message.answer("У тебя уже есть доступ. Используй /mylink.")
            return
        add_user(telegram_id, name, email, vless_link)
        qr = make_qr(vless_link)
        await message.answer(
            f"Добро пожаловать, {name}! Доступ активирован ✅\n\n"
            f"Твоя ссылка:\n<code>{vless_link}</code>\n\n"
            "Импортируй её в клиент (Happ, Karing) или отсканируй QR ниже.\n"
            "Потом всегда доступна команда /mylink.",
            parse_mode="HTML",
        )
        await message.answer_photo(qr)
        return

    # 2) Обычный /start
    user = get_user(message.from_user.id)
    if is_admin(message.from_user.id):
        await message.answer(
            "Привет, админ.\n\n"
            "Команды:\n"
            "/addme — выдать доступ себе\n"
            "/adduser <имя> — приглашение для друга\n"
            "/listusers — управление пользователями\n"
            "/mylink — твоя ссылка"
        )
        return
    if user is None:
        await message.answer(
            "Привет. У тебя пока нет доступа к этому боту — обратись к тому, "
            "кто тебя сюда пригласил."
        )
        return
    name, _, _ = user
    await message.answer(
        f"Привет, {name}! Используй /mylink, чтобы получить ссылку для подключения."
    )


# ==================== /mylink ====================

@dp.message(Command("mylink"))
async def cmd_mylink(message: Message):
    user = get_user(message.from_user.id)
    if user is None:
        await message.answer("У тебя нет доступа. Обратись к администратору.")
        return
    name, email, vless_link = user
    qr = make_qr(vless_link)
    await message.answer_photo(
        qr,
        caption=(
            f"Твоя ссылка, {name}:\n\n"
            f"<code>{vless_link}</code>\n\n"
            "Импортируй её в клиент (Happ, Karing и т.п.) или отсканируй QR-код.\n\n"
            "Ссылка привязана к тебе одному — не передавай её другим."
        ),
        parse_mode="HTML",
    )


# ==================== /addme ====================

@dp.message(Command("addme"))
async def cmd_addme(message: Message):
    if not is_admin(message.from_user.id):
        return
    telegram_id = message.from_user.id
    if get_user(telegram_id):
        await message.answer("У тебя уже есть доступ. /mylink — забирай ссылку.")
        return

    name = message.from_user.first_name or "admin"
    email = f"{name.lower()}-{telegram_id}"
    await message.answer("Создаю клиента в панели...")
    try:
        vless_link = await create_client_link(email=email)
    except Exception as e:
        await message.answer(f"Не удалось создать клиента: {e}")
        return

    add_user(telegram_id, name, email, vless_link)
    qr = make_qr(vless_link)
    await message.answer(
        f"Доступ выдан тебе ✅\n\n"
        f"<code>{vless_link}</code>",
        parse_mode="HTML",
    )
    await message.answer_photo(qr)


# ==================== /adduser (приглашение) ====================

@dp.message(Command("adduser"))
async def cmd_adduser(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    if not command.args:
        await message.answer("Использование:\n/adduser <имя>")
        return
    name = command.args.strip()

    token = secrets.token_urlsafe(8)
    email = f"{name}-{token[:6]}"

    await message.answer("Создаю клиента в панели...")
    try:
        vless_link = await create_client_link(email=email)
    except Exception as e:
        await message.answer(f"Не удалось создать клиента: {e}")
        return

    add_invite(token, name, email, vless_link)
    me = await bot.me()
    invite_link = f"https://t.me/{me.username}?start={token}"
    await message.answer(
        f"Приглашение для {name} готово ✅\n\n"
        f"{invite_link}\n\n"
        "Отправь эту ссылку другу — он нажмёт Start, и доступ активируется "
        "на его аккаунт автоматически. Ссылка одноразовая."
    )


# ==================== /listusers + inline-удаление ====================

def users_keyboard(users) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"👤 {name} (ID {tid})", callback_data=f"del:{tid}")]
        for tid, name in users
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.message(Command("removeuser"))
async def cmd_removeuser(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    users = list_users()
    if not users:
        await message.answer("Список пуст — удалять некого.")
        return
    await message.answer(
        "Выбери пользователя, которого удалить (VPN отключится сразу):",
        reply_markup=users_keyboard(users),
    )


@dp.message(Command("listusers"))
async def cmd_listusers(message: Message):
    if not is_admin(message.from_user.id):
        return
    users = list_users()
    if not users:
        await message.answer("Список пуст.")
        return
    await message.answer(
        "Пользователи (нажми, чтобы удалить):",
        reply_markup=users_keyboard(users),
    )


@dp.callback_query(F.data.startswith("del:"))
async def cb_ask_delete(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Только для админа", show_alert=True)
        return
    telegram_id = int(callback.data.split(":")[1])
    user = get_user(telegram_id)
    if not user:
        await callback.answer("Пользователь уже не найден", show_alert=True)
        await callback.message.edit_reply_markup(reply_markup=None)
        return
    name = user[0]
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="🗑 Да, удалить",
            callback_data=f"delok:{telegram_id}",
        ),
        InlineKeyboardButton(text="❌ Отмена", callback_data="delcancel"),
    ]])
    await callback.message.answer(
        f"Удалить {name} (ID {telegram_id})?\n"
        "⚠️ Его VPN перестанет работать сразу.",
        reply_markup=kb,
    )
    await callback.answer()


@dp.callback_query(F.data == "delcancel")
async def cb_cancel_delete(callback: CallbackQuery):
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("Отменено ✅")
    await callback.answer()


@dp.callback_query(F.data.startswith("delok:"))
async def cb_do_delete(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Только для админа", show_alert=True)
        return
    telegram_id = int(callback.data.split(":")[1])
    user = get_user(telegram_id)
    if not user:
        await callback.answer("Уже удалён", show_alert=True)
        return
    name, email, _ = user

    await callback.answer("Удаляю из панели...")
    try:
        await delete_client(email)
    except Exception as e:
        await callback.message.answer(
            f"❌ Клиент НЕ удалён из панели: {e}\n"
            f"Удали вручную, иначе доступ останется."
        )
        return

    remove_user(telegram_id)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        f"✅ {name} (ID {telegram_id}) полностью удалён. VPN отключён."
    )
    await callback.answer()


@dp.message()
async def fallback(message: Message):
    user = get_user(message.from_user.id)
    if user is None and not is_admin(message.from_user.id):
        await message.answer("У тебя нет доступа к этому боту.")
        return
    await message.answer("Не понял команду. Используй /mylink.")


async def main():
    init_db()
    await set_bot_commands()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
