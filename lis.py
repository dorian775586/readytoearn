import asyncio
import logging
import os
import sqlite3
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.types import (
    Message, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton, FSInputFile, WebAppInfo
)
from aiogram.filters import CommandStart
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from dotenv import load_dotenv
from datetime import datetime, timedelta
from aiogram.client.bot import DefaultBotProperties

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

admin_ids = {ADMIN_ID}
logging.basicConfig(level=logging.INFO)

conn = sqlite3.connect("tables.db", check_same_thread=False)
cursor = conn.cursor()

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

class BookingStates(StatesGroup):
    waiting_for_phone = State()
    waiting_for_guest_count = State()
    waiting_for_review = State()
    waiting_for_table = State()
    waiting_for_time = State()

user_booking_data = {}

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
        KeyboardButton(text="🪑 Забронировать"),
        KeyboardButton(text="📅 Моя бронь")
    ],
    [
        KeyboardButton(text="📖 Меню")
    ],
    [
        KeyboardButton(
            text="💻 Веб-интерфейс", 
            web_app=WebAppInfo(url="https://readytoearn.vercel.apppython")
        )
    ]]

    if user_id in admin_ids:
        buttons.append([
            KeyboardButton(text="🛠 Управление"),
            KeyboardButton(text="📜 История")
        ])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


async def get_table_keyboard():
    builder = InlineKeyboardBuilder()
    cursor.execute("SELECT id FROM tables ORDER BY id")
    tables = cursor.fetchall()
    for (table_id,) in tables:
        builder.button(text=f"🟢 Стол {table_id}", callback_data=f"book_{table_id}")
    builder.adjust(2)
    return builder.as_markup()

def get_time_keyboard(table_id: int):
    builder = InlineKeyboardBuilder()
    cursor.execute("""
        SELECT time_slot FROM bookings 
        WHERE table_id = ? 
          AND datetime(booking_for) > datetime('now')
    """, (table_id,))
    busy_slots = {row[0] for row in cursor.fetchall()}

    for slot in get_time_slots():
        if slot not in busy_slots:
            builder.button(text=slot, callback_data=f"time_{table_id}_{slot}")
    builder.adjust(3)
    return builder.as_markup()

@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer_photo(
        photo="https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcQbh6M8aJwxylo8aI1B-ceUHaiOyEnA425a0A&s",
        caption="<b>Рестобар Белый Лис</b> приветствует вас!\nТут вы можете дистанционно забронировать любой понравившийся столик!",
        reply_markup=get_reply_keyboard(message.from_user.id)
    )

@dp.message(F.text == "🪑 Забронировать")
async def handle_book_button(message: Message, state: FSMContext):
    user_id = message.from_user.id
    cursor.execute("""
        SELECT 1 FROM bookings 
        WHERE user_id = ? 
          AND datetime(booking_for) > datetime('now')
    """, (user_id,))
    if cursor.fetchone():
        await message.answer("У вас уже есть активная бронь.", reply_markup=get_reply_keyboard(user_id))
        return

    keyboard = await get_table_keyboard()
    await message.answer("Выберите столик:", reply_markup=keyboard)
    await state.set_state(BookingStates.waiting_for_table)

@dp.callback_query(F.data.startswith("book_"))
async def handle_table_selection(callback: CallbackQuery, state: FSMContext):
    table_id = int(callback.data.split("_")[1])
    user_booking_data[callback.from_user.id] = {"table_id": table_id}
    await callback.message.edit_text(f"Стол {table_id} выбран. Выберите время:", reply_markup=get_time_keyboard(table_id))
    await state.set_state(BookingStates.waiting_for_time)

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

    cursor.execute("""
        INSERT INTO bookings (user_id, user_name, phone, table_id, time_slot, booked_at, booking_for)
        VALUES (?, ?, ?, ?, ?, datetime('now'), ?)
    """, (message.from_user.id, message.from_user.full_name, phone, table_id, time_slot, booking_for.strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()

    await message.answer(
        f"🗕️ Бронь подтверждена: стол {table_id}, время {time_slot}, гостей: {guests}",
        reply_markup=get_reply_keyboard(message.from_user.id)
    )
    await state.clear()

@dp.message(F.text == "📅 Моя бронь")
async def handle_my_booking_button(message: Message):
    user_id = message.from_user.id
    cursor.execute("""
        SELECT booking_id, table_id, time_slot FROM bookings
        WHERE user_id = ? 
          AND datetime(booking_for) > datetime('now')
        ORDER BY booking_for LIMIT 1
    """, (user_id,))
    row = cursor.fetchone()
    if row:
        booking_id, table_id, time_slot = row
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отменить бронь", callback_data=f"cancel_{booking_id}")]
        ])
        await message.answer(f"🪑 Ваша бронь: стол {table_id} на {time_slot}.", reply_markup=kb)
    else:
        await message.answer("У вас нет активной брони.", reply_markup=get_reply_keyboard(user_id))

@dp.message(F.text == "📖 Меню")
async def show_menu(message: Message):
    media_folder = "media"
    photos = [
        "menu1.jpg", "menu2.jpg", "menu3.jpg",
        "menu4.jpg", "menu5.jpg", "menu6.jpg"
    ]

    for photo_name in photos:
        photo_path = os.path.join(media_folder, photo_name)
        if os.path.exists(photo_path):
            photo = FSInputFile(photo_path)
            await message.answer_photo(photo=photo)
        else:
            await message.answer(f"Файл {photo_name} не найден.")

@dp.message(F.text == "🛠 Управление")
async def handle_admin_view(message: Message):
    if message.from_user.id not in admin_ids:
        await message.answer("Недостаточно прав.")
        return
    cursor.execute("""
        SELECT booking_id, user_id, user_name, table_id, time_slot, booking_for FROM bookings
        WHERE datetime(booking_for) > datetime('now')
        ORDER BY booking_for
    """)
    rows = cursor.fetchall()
    if not rows:
        await message.answer("Нет активных броней.")
        return

    for row in rows:
        booking_id, user_id, user_name, table_id, time_slot, booking_for = row
        label = "[Вы] " if user_id == message.from_user.id else ""
        text = f"{label}<b>{user_name}</b> — стол {table_id}, время {time_slot}, дата: {booking_for}"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отменить", callback_data=f"cancel_{booking_id}")]
        ])
        await message.answer(text, reply_markup=kb)

@dp.message(F.text == "📜 История")
async def handle_history(message: Message):
    if message.from_user.id not in admin_ids:
        await message.answer("Недостаточно прав.")
        return

    cursor.execute("""
        SELECT booking_id, user_id, user_name, table_id, time_slot, booking_for, booked_at FROM bookings
        ORDER BY booking_for DESC
        LIMIT 20
    """)
    rows = cursor.fetchall()
    if not rows:
        await message.answer("История броней пуста.")
        return

    for row in rows:
        booking_id, user_id, user_name, table_id, time_slot, booking_for, booked_at = row
        label = "[Вы] " if user_id == message.from_user.id else ""
        text = (f"{label}<b>{user_name}</b> — стол {table_id}, время {time_slot}, "
                f"Дата брони: {booking_for}, Забронировано: {booked_at}")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отменить", callback_data=f"cancel_{booking_id}")]
        ])
        await message.answer(text, reply_markup=kb)

@dp.callback_query(F.data.startswith("cancel_"))
async def handle_cancel_booking(callback: CallbackQuery):
    booking_id = int(callback.data.split("_")[1])
    cursor.execute("DELETE FROM bookings WHERE booking_id = ?", (booking_id,))
    conn.commit()
    await callback.message.edit_text("Бронь отменена.")
    await callback.answer()

if __name__ == "__main__":
    asyncio.run(dp.start_polling(bot))