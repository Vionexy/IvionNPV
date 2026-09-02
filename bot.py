import re
from panel import create_client_link, delete_client
import asyncio
import io
import logging
import sqlite3

import qrcode
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, BufferedInputFile
from config import BOT_TOKEN, ADMIN_IDS, DB_PATH, PANEL_WS_INBOUND_ID

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            vless_link TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def get_user(telegram_id: int):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT name, vless_link FROM users WHERE telegram_id = ?",
        (telegram_id,),
    ).fetchone()
    conn.close()
    return row


def add_user(telegram_id: int, name: str, vless_link: str) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO users (telegram_id, name, vless_link) VALUES (?, ?, ?)",
        (telegram_id, name, vless_link),
    )
    conn.commit()
    conn.close()


def remove_user(telegram_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("DELETE FROM users WHERE telegram_id = ?", (telegram_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def extract_uuid(vless_link: str):
    m = re.match(r"vless://([^@]+)@", vless_link)
    return m.group(1) if m else None


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


@dp.message(Command("start"))
async def cmd_start(message: Message):
    user = get_user(message.from_user.id)
    if is_admin(message.from_user.id):
        await message.answer(
            "Привет, админ.\n\n"
            "Команды:\n"
            "/adduser <telegram_id> <имя> — добавить друга (ссылку создаст сам)\n"
            "/removeuser <telegram_id> — убрать доступ\n"
            "/listusers — список всех, кому выдан доступ\n"
            "/mylink — получить свою ссылку (если добавлен себе)"
        )
        return
    if user is None:
        await message.answer(
            "Привет. У тебя пока нет доступа к этому боту — обратись к тому, "
            "кто тебя сюда пригласил."
        )
        return
    name, _ = user
    await message.answer(
        f"Привет, {name}! Используй /mylink, чтобы получить ссылку для подключения."
    )


@dp.message(Command("mylink"))
async def cmd_mylink(message: Message):
    user = get_user(message.from_user.id)
    if user is None:
        await message.answer("У тебя нет доступа. Обратись к администратору.")
        return
    name, vless_link = user
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


@dp.message(Command("adduser"))
async def cmd_adduser(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    if not command.args:
        await message.answer("Использование:\n/adduser <telegram_id> <имя>")
        return
    parts = command.args.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование:\n/adduser <telegram_id> <имя>")
        return
    try:
        telegram_id = int(parts[0])
    except ValueError:
        await message.answer("telegram_id должен быть числом.")
        return
    name = parts[1]
    await message.answer("Создаю клиента в панели...")
    try:
        vless_link = await create_client_link(email=f"{name}-{telegram_id}")
    except Exception as e:
        await message.answer(f"Не удалось создать клиента: {e}")
        return
    add_user(telegram_id, name, vless_link)
    await message.answer(f"Добавлено: {name} (ID {telegram_id})")


@dp.message(Command("removeuser"))
async def cmd_removeuser(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    if not command.args:
        await message.answer("Использование:\n/removeuser <telegram_id>")
        return
    try:
        telegram_id = int(command.args.strip())
    except ValueError:
        await message.answer("telegram_id должен быть числом.")
        return
    user = get_user(telegram_id)
    if not user:
        await message.answer("Такого ID не было в списке.")
        return
    name, vless_link = user
    client_uuid = extract_uuid(vless_link)
    if not client_uuid:
        await message.answer("Не смог разобрать ссылку — удали клиента в панели вручную.")
        return
    await message.answer("Удаляю клиента из панели...")
    try:
        await delete_client(client_uuid, PANEL_WS_INBOUND_ID)
    except Exception as e:
        await message.answer(
            f"Клиент НЕ удалён из панели: {e}\n"
            f"Удали его вручную, иначе доступ останется. "
            f"UUID: {client_uuid}"
        )
        return
    remove_user(telegram_id)
    await message.answer(f"Доступ для {name} (ID {telegram_id}) полностью удалён.")


@dp.message(Command("listusers"))
async def cmd_listusers(message: Message):
    if not is_admin(message.from_user.id):
        return
    users = list_users()
    if not users:
        await message.answer("Список пуст.")
        return
    text = "\n".join(f"{tid} — {name}" for tid, name in users)
    await message.answer(text)


@dp.message()
async def fallback(message: Message):
    user = get_user(message.from_user.id)
    if user is None and not is_admin(message.from_user.id):
        await message.answer("У тебя нет доступа к этому боту.")
        return
    await message.answer("Не понял команду. Используй /mylink.")


async def main():
    init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
