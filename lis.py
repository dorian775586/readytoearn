import os
import logging
import asyncio
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F, types
from aiogram.enums import ParseMode, ContentType
from aiogram.types import (
    Message, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
)
from aiogram.filters import CommandStart
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.client.bot import DefaultBotProperties

from aiohttp import web
import asyncpg
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
DATABASE_URL = os.getenv("DATABASE_URL")  # postgres://user:pass@host:port/dbname

logging.basicConfig(level=logging.INFO)
admin_ids = {ADMIN_ID}

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

class BookingStates(StatesGroup):
    waiting_for_guest_count = State()
    waiting_for_phone = State()

# Подключение к БД
db_pool: asyncpg.pool.Pool = None

async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)
    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                booking_id SERIAL PRIMARY KEY,
                user_id BIGINT,
                user_name TEXT,
                phone TEXT,
                table_id INT,
                time_slot TEXT,
                guests INT,
                booked_at TIMESTAMP,
                booking_for TIMESTAMP
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS tables (
                id INT PRIMARY KEY
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                user_id BIGINT PRIMARY KEY
            );
        """)
        # Пример столов
        existing = await conn.fetchval("SELECT COUNT(*) FROM tables")
        if existing == 0:
            await conn.executemany("INSERT INTO tables (id) VALUES ($1)", [(1,), (2,), (3,), (4,), (5,), (6,)])

def get_time_slots():
    slots = []
    start = datetime.strptime("12:00", "%H:%M")
    end = datetime.strptime("23:00", "%H:%M")
    while start <= end:
        slots.append(start.strftime("%H:%M"))
        start += timedelta(minutes=30)
    return slots

def get_reply_keyboard(user_id=None):
    buttons = [[
        KeyboardButton(text="📅 Забронировать"),
        KeyboardButton(text="📝 Моя бронь")
    ],
    [
        KeyboardButton(text="📖 Меню")
    ],
    [
        KeyboardButton(
            text="🌐 Веб-интерфейс", 
            web_app=WebAppInfo(url="https://gitrepo-drab.vercel.app")
        )
    ]]

    if user_id in admin_ids:
        buttons.append([KeyboardButton(text="🛠 Управление"), KeyboardButton(text="📜 История")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

async def get_table_keyboard():
    builder = InlineKeyboardBuilder()
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT id FROM tables ORDER BY id")
        for record in rows:
            builder.button(text=f"🍽 Стол {record['id']}", callback_data=f"book_{record['id']}")
    builder.adjust(2)
    return builder.as_markup()

async def get_time_keyboard(table_id: int):
    builder = InlineKeyboardBuilder()
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT time_slot FROM bookings 
            WHERE table_id = $1 AND booking_for > NOW()
        """, table_id)
        busy_slots = {record['time_slot'] for record in rows}

    for slot in get_time_slots():
        if slot not in busy_slots:
            builder.button(text=slot, callback_data=f"time_{table_id}_{slot}")
    builder.adjust(3)
    return builder.as_markup()

user_booking_data = {}

@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer_photo(
        photo="https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcQbh6M8aJwxylo8aI1B-ceUHaiOyEnA425a0A&s",
        caption="<b>Рестобар Белый Лис</b> приветствует вас!\nЗдесь вы можете дистанционно забронировать любой понравившийся столик!",
        reply_markup=get_reply_keyboard(message.from_user.id)
    )

@dp.message(F.text == "📅 Забронировать")
async def handle_book_button(message: Message, state: FSMContext):
    user_id = message.from_user.id
    async with db_pool.acquire() as conn:
        existing = await conn.fetchrow("""
            SELECT 1 FROM bookings WHERE user_id = $1 AND booking_for > NOW()
        """, user_id)
    if existing:
        await message.answer("У вас уже есть активная бронь.", reply_markup=get_reply_keyboard(user_id))
        return
    keyboard = await get_table_keyboard()
    await message.answer("Выберите столик:", reply_markup=keyboard)
    await state.set_state(BookingStates.waiting_for_guest_count)

@dp.callback_query(F.data.startswith("book_"))
async def handle_table_selection(callback: CallbackQuery, state: FSMContext):
    table_id = int(callback.data.split("_")[1])
    user_booking_data[callback.from_user.id] = {"table_id": table_id}
    await callback.message.edit_text(f"Стол {table_id} выбран. Выберите время:", reply_markup=await get_time_keyboard(table_id))
    await state.set_state(BookingStates.waiting_for_guest_count)

@dp.callback_query(F.data.startswith("time_"))
async def handle_time_selection(callback: CallbackQuery, state: FSMContext):
    _, table_id, slot = callback.data.split("_")
    user_data = user_booking_data.get(callback.from_user.id, {})
    user_data.update({"time_slot": slot})
    user_booking_data[callback.from_user.id] = user_data

    await callback.message.edit_text(f"Вы выбрали стол {table_id} на {slot}. Сколько будет гостей?")
    await state.set_state(BookingStates.waiting_for_guest_count)

@dp.message(BookingStates.waiting_for_guest_count)
async def handle_guest_count(message: Message, state: FSMContext):
    guests = message.text.strip()
    if not guests.isdigit() or int(guests) < 1:
        await message.answer("Пожалуйста, введите корректное количество гостей.")
        return
    user_booking_data[message.from_user.id]["guests"] = int(guests)

    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Отправить номер телефона", request_contact=True)]], 
        resize_keyboard=True
    )
    await message.answer("Теперь отправьте свой номер телефона:", reply_markup=kb)
    await state.set_state(BookingStates.waiting_for_phone)

@dp.message(BookingStates.waiting_for_phone, F.contact)
async def handle_phone(message: Message, state: FSMContext):
    phone = message.contact.phone_number
    user_data = user_booking_data.get(message.from_user.id, {})
    table_id = user_data.get("table_id")
    time_slot = user_data.get("time_slot")
    guests = user_data.get("guests")

    now = datetime.now()
    booking_for = now.replace(hour=int(time_slot[:2]), minute=int(time_slot[3:]), second=0, microsecond=0)
    if booking_for < now:
        booking_for += timedelta(days=1)

    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO bookings (user_id, user_name, phone, table_id, time_slot, guests, booked_at, booking_for)
            VALUES ($1, $2, $3, $4, $5, $6, NOW(), $7)
        """, message.from_user.id, message.from_user.full_name, phone, table_id, time_slot, guests, booking_for)

    await message.answer(
        f"✅ Бронь подтверждена: стол {table_id}, время {time_slot}, гостей: {guests}",
        reply_markup=get_reply_keyboard(message.from_user.id)
    )
    await state.clear()
    user_booking_data.pop(message.from_user.id, None)

@dp.message(F.text == "📝 Моя бронь")
async def handle_my_booking_button(message: Message):
    user_id = message.from_user.id
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT booking_id, table_id, time_slot FROM bookings
            WHERE user_id = $1 AND booking_for > NOW()
            ORDER BY booking_for LIMIT 1
        """, user_id)
    if row:
        booking_id, table_id, time_slot = row
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отменить бронь", callback_data=f"cancel_{booking_id}")]
        ])
        await message.answer(f"📝 Ваша бронь: стол {table_id} на {time_slot}.", reply_markup=kb)
    else:
        await message.answer("У вас нет активной брони.", reply_markup=get_reply_keyboard(user_id))

@dp.message(F.text == "📖 Меню")
async def show_menu(message: Message):
    photos_urls = [
        "https://raw.githubusercontent.com/youruser/repo/main/menu1.jpg",
        "https://raw.githubusercontent.com/youruser/repo/main/menu2.jpg",
        "https://raw.githubusercontent.com/youruser/repo/main/menu3.jpg",
        "https://raw.githubusercontent.com/youruser/repo/main/menu4.jpg",
        "https://raw.githubusercontent.com/youruser/repo/main/menu5.jpg",
        "https://raw.githubusercontent.com/youruser/repo/main/menu6.jpg",
    ]
    for url in photos_urls:
        await message.answer_photo(photo=url)

@dp.callback_query(F.data.startswith("cancel_"))
async def handle_cancel_booking(callback: CallbackQuery):
    booking_id = int(callback.data.split("_")[1])
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM bookings WHERE booking_id = $1", booking_id)
    await callback.message.edit_text("❌ Ваша бронь отменена.", reply_markup=get_reply_keyboard(callback.from_user.id))

async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
