import os
import logging
from datetime import datetime, timedelta, date
# import threading # Больше не нужен, т.к. threaded=False
import requests 
import json 

from flask import Flask, request, jsonify
from telebot import TeleBot, types
import psycopg2
from psycopg2.extras import RealDictCursor
from flask_cors import CORS

# =========================
# ЛОГИРОВАНИЕ
# =========================
logging.basicConfig(level=logging.INFO)
print("Логирование настроено.") 

# =========================
# ENV
# =========================
BOT_TOKEN = (os.environ.get("BOT_TOKEN") or "").strip()
DATABASE_URL = (os.environ.get("DATABASE_URL") or "").strip()
ADMIN_ID_ENV = (os.environ.get("ADMIN_ID") or "").strip()
WEBAPP_URL = (os.environ.get("WEBAPP_URL") or "https://gitrepo-drab.vercel.app").strip() 
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL") 

print(f"BOT_TOKEN_STATUS: {'SET' if BOT_TOKEN else 'NOT SET'}")
print(f"DATABASE_URL_STATUS: {'SET' if DATABASE_URL else 'NOT SET'}")
print(f"RENDER_EXTERNAL_URL_STATUS: {'SET' if RENDER_EXTERNAL_URL else 'NOT SET'}")

if not BOT_TOKEN:
    raise RuntimeError("Ошибка: BOT_TOKEN пуст или не задан!")
if not DATABASE_URL:
    raise RuntimeError("Ошибка: DATABASE_URL не задан!")
if not RENDER_EXTERNAL_URL: 
    raise RuntimeError("Ошибка: RENDER_EXTERNAL_URL не задан! Проверьте переменные окружения на Render.")


# Исправление DATABASE_URL для Render, если порт отсутствует
if "render.com/" in DATABASE_URL and ":5432" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace(".render.com/", ".render.com:5432/")

ADMIN_ID = None
if ADMIN_ID_ENV:
    try:
        ADMIN_ID = int(ADMIN_ID_ENV)
        print(f"ADMIN_ID установлен: {ADMIN_ID}")
    except ValueError:
        print(f"Предупреждение: ADMIN_ID ('{ADMIN_ID_ENV}') не является числом; админ-функции отключены.")

# =========================
# КОНСТАНТЫ МЕНЮ (ТОЛЬКО ТЕКСТ)
# =========================
RESTAURANT_NAME = "Белый Лис"

MENU_CATEGORIES = [
    "🥣 Закуски (Холодные)",
    "🌶️ Закуски (Горячие/Супы)",
    "🥗 Салаты",
    "🍔 Бургеры",
    "🌯 Сэндвичи & Роллы",
    "🍖 Основное (Говядина)",
    "🐟 Основное (Рыба/Свинина)",
    "🍗 Основное (Курица/Утка)",
    "🥩 Премиум Стейки",
    "☕ Десерты & Напитки",
]

# =========================
# DB INIT
# =========================
def db_connect():
    # Используем try-except для подключения, чтобы ловить потенциальные ошибки БД
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    print("Инициализация базы данных...")
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                # Создание таблицы столов
                cur.execute("""
                CREATE TABLE IF NOT EXISTS tables (
                    id INT PRIMARY KEY
                );
                """)
                # Создание таблицы бронирований
                cur.execute("""
                CREATE TABLE IF NOT EXISTS bookings (
                    booking_id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    user_name VARCHAR(255),
                    phone TEXT,
                    guests INT,
                    table_id INT NOT NULL,
                    time_slot TEXT NOT NULL,
                    booked_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    booking_for TIMESTAMP
                );
                """)
                # Добавление столбцов, если они отсутствуют (для совместимости)
                cur.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS user_id BIGINT;")
                cur.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS user_name TEXT;")
                cur.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS phone TEXT;")
                cur.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS guests INT;")
                cur.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS booking_for TIMESTAMP;")
                
                # Создание индексов для оптимизации запросов
                cur.execute("CREATE INDEX IF NOT EXISTS idx_bookings_conflict ON bookings (table_id, booking_for);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_bookings_user_active ON bookings (user_id, booking_for DESC);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_bookings_future_time ON bookings (booking_for);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_bookings_booked_at ON bookings (booked_at DESC);")

                # Инициализация столов (1 до 20)
                TARGET_TABLE_COUNT = 20
                cur.execute("SELECT id FROM tables ORDER BY id ASC;")
                existing_table_ids = [row['id'] for row in cur.fetchall()]
                tables_to_add = [i for i in range(1, TARGET_TABLE_COUNT + 1) if i not in existing_table_ids]
                
                if tables_to_add:
                    insert_values = ",".join(f"({i})" for i in tables_to_add)
                    cur.execute(f"INSERT INTO tables (id) VALUES {insert_values};")
                    print(f"База данных: Добавлено {len(tables_to_add)} новых столов (ID: {tables_to_add}).")
                else:
                    print("База данных: Все столы до 20 уже существуют.")

            conn.commit()
        print("База данных: Инициализация завершена успешно.")
    except Exception as e:
        print(f"Ошибка инициализации базы: {e}")

# =========================
# BOT & APP
# =========================
# КРИТИЧЕСКОЕ ИЗМЕНЕНИЕ: threaded=False, чтобы избежать конфликтов с Flask/Gunicorn и Webhook.
bot = TeleBot(BOT_TOKEN, parse_mode="HTML", threaded=False) 
app = Flask(__name__)
CORS(app)

with app.app_context(): 
    init_db()

# =========================
# HELPERS (UI)
# =========================
def main_reply_kb(user_id: int, user_name: str) -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    
    # Передача необходимых параметров в WebApp URL
    web_app_url = f"{WEBAPP_URL}?user_id={user_id}&user_name={user_name}&bot_url={RENDER_EXTERNAL_URL}"
    
    row1 = [
        types.KeyboardButton(text="🗓️ Забронировать", web_app=types.WebAppInfo(url=web_app_url)),
        types.KeyboardButton("📋 Моя бронь"),
    ]
    row2 = [types.KeyboardButton("📖 Меню")]
    kb.row(*row1)
    kb.row(*row2)
    if ADMIN_ID and str(user_id) == str(ADMIN_ID):
        kb.row(types.KeyboardButton("🛠 Управление"), types.KeyboardButton("🗂 История"))
    return kb

# =========================
# COMMANDS & BUTTONS
# ПОРЯДОК ХЕНДЛЕРОВ ВАЖЕН: КОНКРЕТНЫЕ СНАЧАЛА, УНИВЕРСАЛЬНЫЙ (default_handler) В КОНЦЕ
# =========================

@bot.message_handler(commands=["start"])
def cmd_start(message: types.Message):
    print(f"[{datetime.now()}] (Обработчик) Получена команда /start от user_id: {message.from_user.id}")
    user_id = message.from_user.id
    user_name = message.from_user.full_name or "Неизвестный"
    
    try:
        # Отправка текстового сообщения с клавиатурой
        bot.send_message(
            message.chat.id,
            f"<b>Рестобар «{RESTAURANT_NAME}»</b> приветствует вас!\nТут вы можете дистанционно забронировать любой понравившийся столик!",
            reply_markup=main_reply_kb(user_id, user_name),
            parse_mode="HTML"
        )
        print(f"[{datetime.now()}] (Обработчик) Отправлено приветственное текстовое сообщение для user_id: {user_id}")
    except Exception as e:
        print(f"[{datetime.now()}] (Обработчик) КРИТИЧЕСКАЯ ОШИБКА при отправке приветственного сообщения user_id: {user_id}: {e}")
        try:
            bot.send_message(message.chat.id, "Извините, произошла ошибка при загрузке приветствия. Пожалуйста, проверьте мой статус или попробуйте позже.")
            print(f"[{datetime.now()}] (Обработчик) Отправлено сообщение об ошибке пользователю {user_id}")
        except Exception as e_inner:
            print(f"[{datetime.now()}] (Обработчик) НЕ УДАЛОСЬ ОТПРАВИТЬ СООБЩЕНИЕ ОБ ОШИБКЕ пользователю {user_id}: {e_inner}")


@bot.message_handler(commands=["history"])
def cmd_history(message: types.Message):
    print(f"[{datetime.now()}] (Обработчик) Получена команда /history от user_id: {message.from_user.id}")
    if not ADMIN_ID or str(message.chat.id) != str(ADMIN_ID):
        bot.send_message(message.chat.id, "У вас нет прав для этой команды.")
        return
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT booking_id, user_name, table_id, time_slot, booked_at, booking_for
                    FROM bookings
                    ORDER BY booked_at DESC
                    LIMIT 50;
                """)
                rows = cur.fetchall()
        if not rows:
            bot.send_message(message.chat.id, "История пуста.")
            return
        text = "<b>История бронирований (последние 50):</b>\n\n"
        for r in rows:
            booking_date = r['booking_for'].strftime("%d.%m.%Y")
            text += f"#{r['booking_id']} — {r['user_name']}, стол {r['table_id']}, {r['time_slot']}, {booking_date}\n"
        bot.send_message(message.chat.id, text, parse_mode="HTML")
    except Exception as e:
        bot.send_message(message.chat.id, f"Ошибка истории: {e}")


@bot.message_handler(func=lambda m: "Моя бронь" in m.text) # Более надежная проверка
def on_my_booking(message: types.Message):
    print(f"[{datetime.now()}] (Обработчик) Нажата кнопка 'Моя бронь' от user_id: {message.from_user.id}")
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                # Ищем активную бронь (booking_for > NOW())
                cur.execute("""
                    SELECT booking_id, table_id, time_slot, booking_for
                    FROM bookings
                    WHERE user_id=%s AND booking_for > NOW()
                    ORDER BY booked_at DESC
                    LIMIT 1;
                """, (message.from_user.id,))
                row = cur.fetchone()
        if not row:
            user_id = message.from_user.id
            user_name = message.from_user.full_name or "Неизвестный"
            bot.send_message(message.chat.id, "У вас нет активной брони.", reply_markup=main_reply_kb(user_id, user_name))
            return
        
        booking_date = row['booking_for'].strftime("%d.%m.%Y")
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton(text="❌ Отменить бронь", callback_data=f"cancel_{row['booking_id']}"))
        bot.send_message(message.chat.id, f"🔖 Ваша бронь: стол {row['table_id']} на {row['time_slot']} ({booking_date}).", reply_markup=kb)
    except Exception as e:
        bot.send_message(message.chat.id, f"Ошибка: {e}")

@bot.message_handler(func=lambda m: "Меню" in m.text) # Более надежная проверка
def on_menu(message: types.Message):
    print(f"[{datetime.now()}] (Обработчик) Нажата кнопка 'Меню' от user_id: {message.from_user.id}")
    kb = types.InlineKeyboardMarkup(row_width=2) 
    
    buttons = []
    for name in MENU_CATEGORIES: 
        buttons.append(types.InlineKeyboardButton(name, callback_data=f"menu_cat_{name}"))
        
    kb.add(*buttons)
    
    try:
        bot.send_message(
            message.chat.id, 
            "🍽️ Выберите интересующий вас раздел меню:",
            reply_markup=kb
        )
        print(f"[{datetime.now()}] (Обработчик) Отправлено меню с категориями для user_id: {message.from_user.id}")
    except Exception as e:
        print(f"[{datetime.now()}] (Обработчик) Ошибка при отправке меню user_id: {message.from_user.id}: {e}")
        bot.send_message(message.chat.id, "Извините, произошла ошибка при загрузке меню. Попробуйте позже.")


# =========================
# АДМИН-ПАНЕЛЬ
# =========================
@bot.message_handler(func=lambda m: "Управление" in m.text) # Более надежная проверка
def on_admin_panel(message: types.Message):
    print(f"[{datetime.now()}] (Обработчик) Нажата кнопка 'Управление' от user_id: {message.from_user.id}")
    if not ADMIN_ID or str(message.chat.id) != str(ADMIN_ID):
        bot.send_message(message.chat.id, "У вас нет прав для этой команды.")
        return
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                # Выводим активные бронирования
                cur.execute("""
                    SELECT booking_id, user_name, table_id, time_slot, booking_for, phone
                    FROM bookings
                    WHERE booking_for > NOW()
                    ORDER BY booking_for ASC;
                """)
                rows = cur.fetchall()
        if not rows:
            bot.send_message(message.chat.id, "Активных бронирований нет.")
            return
        
        for r in rows:
            booking_date = r['booking_for'].strftime("%d.%m.%Y")
            text = f"🔖 Бронь #{r['booking_id']} — {r['user_name']}\n"
            text += f"   - Стол: {r['table_id']}\n"
            text += f"   - Время: {r['time_slot']} ({booking_date})\n"
            text += f"   - Телефон: {r['phone']}\n"
            
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton(text="❌ Отменить", callback_data=f"admin_cancel_{r['booking_id']}"))
            bot.send_message(message.chat.id, text, parse_mode="HTML", reply_markup=kb)

    except Exception as e:
        bot.send_message(message.chat.id, f"Ошибка админ-панели: {e}")

@bot.message_handler(func=lambda m: "🗂 История" in m.text) # Более надежная проверка
def on_history_btn(message: types.Message):
    # Хендлер для кнопки, который вызывает хендлер команды /history
    return cmd_history(message)

# =========================
# CALLBACKS
# =========================

@bot.callback_query_handler(func=lambda c: c.data.startswith("menu_cat_"))
def on_menu_category_select(call: types.CallbackQuery):
    print(f"[{datetime.now()}] (Обработчик) Получен callback от кнопки меню '{call.data}' от user_id: {call.from_user.id}")
    category_name = call.data.split("menu_cat_")[1]
    
    # Отправляем текстовое описание категории
    try:
        bot.send_message(
            call.message.chat.id, 
            f"Раздел: <b>{category_name}</b>\n\nЗдесь должно быть описание или список блюд.", 
            parse_mode="HTML"
        )
        
        # Повторно отправляем клавиатуру с категориями
        kb = types.InlineKeyboardMarkup(row_width=2)
        buttons = [types.InlineKeyboardButton(name, callback_data=f"menu_cat_{name}") for name in MENU_CATEGORIES]
        kb.add(*buttons)
        bot.send_message(
            call.message.chat.id, 
            "⬇️ Выберите следующий раздел:",
            reply_markup=kb
        )

        bot.answer_callback_query(call.id, text=f"Открываю: {category_name}")
        print(f"[{datetime.now()}] (Обработчик) Отправлено текстовое меню для категории '{category_name}' user_id: {call.from_user.id}")
        
    except Exception as e:
        logging.error(f"[{datetime.now()}] (Обработчик) Ошибка при отправке текстового меню для user_id: {call.from_user.id}: {e}")
        bot.send_message(call.message.chat.id, f"Произошла ошибка при загрузке раздела <b>{category_name}</b>.", parse_mode="HTML")
        bot.answer_callback_query(call.id, text="Ошибка загрузки.", show_alert=True)

@bot.callback_query_handler(func=lambda c: c.data.startswith("cancel_"))
def on_cancel_user(call: types.CallbackQuery):
    print(f"[{datetime.now()}] (Обработчик) Получен callback для отмены брони пользователем '{call.data}' от user_id: {call.from_user.id}")
    booking_id = int(call.data.split("_")[1])
    try:
        booking_info = None
        rows_deleted = 0
        
        with db_connect() as conn:
            with conn.cursor() as cur:
                # 1. Получаем информацию о бронировании (для уведомления админа)
                cur.execute("""
                    SELECT user_id, user_name, table_id, time_slot, booking_for, phone, guests
                    FROM bookings
                    WHERE booking_id=%s AND user_id=%s;
                """, (booking_id, call.from_user.id))
                booking_info = cur.fetchone()
                
                # 2. Удаляем бронирование (только если user_id совпадает)
                cur.execute("DELETE FROM bookings WHERE booking_id=%s AND user_id=%s;", (booking_id, call.from_user.id))
                rows_deleted = cur.rowcount
                conn.commit()
        
        if rows_deleted > 0:
            # Обновляем сообщение в чате
            bot.edit_message_text("✅ Бронь отменена.", chat_id=call.message.chat.id, message_id=call.message.id)
            print(f"[{datetime.now()}] (Обработчик) Бронь #{booking_id} отменена пользователем {call.from_user.id}")
            
            # 3. Уведомление администратора
            if ADMIN_ID and booking_info:
                try:
                    booking_date = booking_info['booking_for'].strftime("%d.%m.%Y")
                    user_id = booking_info['user_id']
                    user_name = booking_info['user_name'] or call.from_user.full_name or 'Неизвестный пользователь'
                    user_link = f'<a href="tg://user?id={user_id}">{user_name}</a>' if user_id else user_name
                    
                    message_text = (
                        f"❌ Бронь отменена пользователем:\n"
                        f"ID Брони: <b>#{booking_id}</b>\n"
                        f"Пользователь: {user_link}\n"
                        f"Стол: {booking_info['table_id']}\n"
                        f"Дата: {booking_date}\n"
                        f"Время: {booking_info['time_slot']}\n"
                        f"Гостей: {booking_info.get('guests', 'N/A')}\n"
                        f"Телефон: {booking_info.get('phone', 'Не указан')}"
                    )
                    bot.send_message(ADMIN_ID, message_text, parse_mode="HTML")
                    print(f"[{datetime.now()}] (Обработчик) Уведомление админа об отмене брони #{booking_id} отправлено.")
                except Exception as e:
                    print(f"[{datetime.now()}] (Обработчик) Не удалось уведомить админа об отмене брони: {e}")

        else:
            bot.answer_callback_query(call.id, "Бронь уже была отменена или не найдена.", show_alert=True)
            print(f"[{datetime.now()}] (Обработчик) Пользователь {call.from_user.id} пытался отменить несуществующую/уже отмененную бронь #{booking_id}")
            
    except Exception as e:
        print(f"[{datetime.now()}] (Обработчик) Ошибка при отмене брони пользователем {call.from_user.id} брони #{booking_id}: {e}")
        bot.answer_callback_query(call.id, f"Ошибка: {e}", show_alert=True)


@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_cancel_"))
def on_cancel_admin(call: types.CallbackQuery):
    print(f"[{datetime.now()}] (Обработчик) Получен callback для отмены брони админом '{call.data}' от user_id: {call.from_user.id}")
    booking_id = int(call.data.split("_")[2])
    if not ADMIN_ID or str(call.from_user.id) != str(ADMIN_ID):
        bot.answer_callback_query(call.id, "У вас нет прав для этого действия.", show_alert=True)
        return
    try:
        booking_info = None
        with db_connect() as conn:
            with conn.cursor() as cur:
                # Получаем информацию о бронировании (для уведомления пользователя)
                cur.execute("SELECT user_id, table_id, time_slot, booking_for FROM bookings WHERE booking_id=%s;", (booking_id,))
                booking_info = cur.fetchone()

                # Удаляем бронирование
                cur.execute("DELETE FROM bookings WHERE booking_id=%s;", (booking_id,))
                conn.commit()
        
        if booking_info:
            user_id = booking_info['user_id']
            booking_date = booking_info['booking_for'].strftime("%d.%m.%Y")
            message_text = f"❌ Ваша бронь отменена администратором.\n\nСтол: {booking_info['table_id']}\nДата: {booking_date}\nВремя: {booking_info['time_slot']}"
            try:
                # Отправляем уведомление пользователю
                bot.send_message(user_id, message_text)
                print(f"[{datetime.now()}] (Обработчик) Уведомление пользователю {user_id} об отмене брони #{booking_id} отправлено.")
            except Exception as e:
                print(f"[{datetime.now()}] (Обработчик) Не удалось уведомить пользователя {user_id} об отмене брони: {e}")

        # Обновляем сообщение в админ-панели
        bot.edit_message_text(f"✅ Бронь #{booking_id} успешно отменена.", chat_id=call.message.chat.id, message_id=call.message.id)
        bot.answer_callback_query(call.id, "Бронь отменена.", show_alert=True)
        print(f"[{datetime.now()}] (Обработчик) Бронь #{booking_id} отменена админом {call.from_user.id}")
    except Exception as e:
        print(f"[{datetime.now()}] (Обработчик) Ошибка при отмене брони админом {call.from_user.id} брони #{booking_id}: {e}")
        bot.answer_callback_query(call.id, f"Ошибка: {e}", show_alert=True)

@bot.message_handler(content_types=['web_app_data'])
def on_webapp_data(message: types.Message):
    print(f"[{datetime.now()}] (Обработчик) ПРИШЛИ ДАННЫЕ ОТ WEBAPP: {message.web_app_data.data}") 
    try:
        data = json.loads(message.web_app_data.data)
        user_id = message.from_user.id
        user_name = message.from_user.full_name or "Неизвестный"
        phone = data.get('phone')
        guests = data.get('guests')
        table_id = data.get('table')
        time_slot = data.get('time')
        date_str = data.get('date')

        if not all([phone, guests, table_id, time_slot, date_str]):
            bot.send_message(user_id, "Ошибка: Не хватает данных для бронирования через WebApp.")
            return

        booking_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        booking_datetime = datetime.combine(booking_date, datetime.strptime(time_slot, '%H:%M').time())

        with db_connect() as conn:
            # Проверка на конфликт
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM bookings WHERE table_id = %s AND booking_for::date = %s AND time_slot = %s;",
                    (table_id, booking_date, time_slot)
                )
                existing_booking = cursor.fetchone()
                if existing_booking:
                    bot.send_message(user_id, f"Стол {table_id} уже забронирован на {date_str} {time_slot}. Пожалуйста, выберите другое время.")
                    return
            
            # Вставка бронирования
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO bookings (user_id, user_name, phone, table_id, time_slot, guests, booked_at, booking_for)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
                    """,
                    (user_id, user_name, phone, table_id, time_slot, guests, datetime.now(), booking_datetime)
                )
                conn.commit()
            
            # Уведомление пользователя
            formatted_date = booking_date.strftime("%d.%m.%Y")
            message_text = f"✅ Ваша бронь успешно оформлена!\n\nСтол: {table_id}\nДата: {formatted_date}\nВремя: {time_slot}"
            bot.send_message(user_id, message_text)

            # Уведомление администратора
            if ADMIN_ID:
                user_link = f'<a href="tg://user?id={user_id}">{user_name}</a>' if user_id else user_name
                admin_message_text = (
                    f"🔔 Новая бронь:\n"
                    f"Пользователь: {user_link}\n"
                    f"Стол: {table_id}\n"
                    f"Дата: {formatted_date}\n"
                    f"Время: {time_slot}\n"
                    f"Гостей: {guests}\n"
                    f"Телефон: {phone}"
                )
                bot.send_message(ADMIN_ID, admin_message_text, parse_mode="HTML")

    except json.JSONDecodeError as e:
        print(f"[{datetime.now()}] (Обработчик) Ошибка парсинга JSON из WebApp: {e}")
        bot.send_message(message.from_user.id, "Ошибка в данных от WebApp. Попробуйте снова.")
    except Exception as e:
        print(f"[{datetime.now()}] (Обработчик) Ошибка обработки WebApp данных: {e}")
        bot.send_message(message.from_user.id, "Произошла ошибка при бронировании. Пожалуйста, попробуйте позже.")
        

# =========================
# BOOKING API
# =========================
@app.route("/book", methods=["POST"])
def book_api():
    print(f"[{datetime.now()}] Получен POST запрос на /book")
    try:
        data = request.json
        user_id = data.get('user_id') or 0
        user_name = data.get('user_name') or 'Неизвестный'
        phone = data.get('phone')
        guests = data.get('guests')
        table_id = data.get('table')
        time_slot = data.get('time')
        date_str = data.get('date')

        if not all([phone, guests, table_id, time_slot, date_str]):
            print(f"[{datetime.now()}] Ошибка: Не хватает данных для бронирования.")
            return {"status": "error", "message": "Не хватает данных для бронирования"}, 400

        booking_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        booking_datetime = datetime.combine(booking_date, datetime.strptime(time_slot, '%H:%M').time())

        # Используем db_connect() вместо прямого вызова psycopg2.connect
        with db_connect() as conn:
            # Проверка на конфликт
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM bookings WHERE table_id = %s AND booking_for::date = %s AND time_slot = %s;",
                    (table_id, booking_date, time_slot)
                )
                existing_booking = cursor.fetchone()
                if existing_booking:
                    print(f"[{datetime.now()}] Ошибка: Стол {table_id} уже забронирован на {date_str} {time_slot}.")
                    return {"status": "error", "message": "Этот стол уже забронирован на это время."}, 409
            
            # Вставка бронирования
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO bookings (user_id, user_name, phone, table_id, time_slot, guests, booked_at, booking_for)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
                    """,
                    (user_id, user_name, phone, table_id, time_slot, guests, datetime.now(), booking_datetime)
                )
                conn.commit()
                print(f"[{datetime.now()}] Бронь создана для user_id: {user_id}, стол: {table_id}, время: {time_slot} {date_str}")
            
        # Уведомление пользователя через Telegram (если бронь прошла через WebApp, но не как WebAppData)
        try:
            formatted_date = booking_date.strftime("%d.%m.%Y")
            message_text = f"✅ Ваша бронь успешно оформлена!\n\nСтол: {table_id}\nДата: {formatted_date}\nВремя: {time_slot}"
            bot.send_message(user_id, message_text)
            print(f"[{datetime.now()}] Уведомление пользователю {user_id} о брони отправлено.")
        except Exception as e:
            print(f"[{datetime.now()}] Не удалось отправить уведомление пользователю {user_id}: {e}")

        # Уведомление администратора
        if ADMIN_ID:
            try:
                formatted_date = booking_date.strftime("%d.%m.%Y")
                user_link = f'<a href="tg://user?id={user_id}">{user_name}</a>' if user_id else user_name
                message_text = (
                    f"🔔 Новая бронь:\n"
                    f"Пользователь: {user_link}\n"
                    f"Стол: {table_id}\n"
                    f"Дата: {formatted_date}\n"
                    f"Время: {time_slot}\n"
                    f"Гостей: {guests}\n"
                    f"Телефон: {phone}"
                )
                bot.send_message(ADMIN_ID, message_text, parse_mode="HTML")
                print(f"[{datetime.now()}] Уведомление админу о новой брони отправлено.")
            except Exception as e:
                print(f"[{datetime.now()}] Не удалось отправить сообщение админу: {e}")

        return {"status": "ok", "message": "Бронь успешно создана"}, 200

    except Exception as e:
        logging.error(f"[{datetime.now()}] Ошибка /book: {e}")
        return {"status": "error", "message": str(e)}, 400

# =========================
# GET BOOKED TIMES
# =========================
@app.route("/get_booked_times", methods=["GET"])
def get_booked_times():
    print(f"[{datetime.now()}] Получен GET запрос на /get_booked_times")
    try:
        table_id = request.args.get('table')
        date_str = request.args.get('date')

        print(f"[{datetime.now()}] get_booked_times: table_id={table_id}, date_str={date_str}") # ОТЛАДКА

        if not all([table_id, date_str]):
            print(f"[{datetime.now()}] Ошибка: Не хватает данных (стол или дата) для get_booked_times.")
            return {"status": "error", "message": "Не хватает данных (стол или дата)"}, 400

        try:
            query_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            print(f"[{datetime.now()}] get_booked_times: query_date={query_date}") # ОТЛАДКА
        except ValueError:
            print(f"[{datetime.now()}] Ошибка: Неверный формат даты для get_booked_times.")
            return {"status": "error", "message": "Неверный формат даты. Ожидается YYYY-MM-DD."}, 400

        with db_connect() as conn:
            with conn.cursor() as cursor:
                # Запрос только по дате, поскольку time_slot уже содержит время
                cursor.execute(
                    "SELECT time_slot FROM bookings WHERE table_id = %s AND booking_for::date = %s;",
                    (table_id, query_date)
                )
                booked_times = [row['time_slot'] for row in cursor.fetchall()]
                print(f"[{datetime.now()}] get_booked_times: Забронированные слоты из БД для стола {table_id} на {query_date}: {booked_times}") # ОТЛАДКА

        # Генерация всех возможных слотов (с 12:00 до 23:00, шаг 30 минут)
        start_time = datetime.combine(query_date, datetime.strptime("12:00", "%H:%M").time())
        end_time = datetime.combine(query_date, datetime.strptime("23:00", "%H:%M").time())
        current_time = start_time
        all_slots = []
        while current_time <= end_time:
            slot_str = current_time.strftime("%H:%M")
            
            # Исключаем слоты, которые уже прошли СЕГОДНЯ
            if query_date == date.today():
                now_time = datetime.now().time()
                slot_as_time = datetime.strptime(slot_str, '%H:%M').time()
                # Добавляем небольшой запас (например, 30 минут), чтобы не бронировать "на сейчас"
                if slot_as_time < now_time:
                    current_time += timedelta(minutes=30)
                    continue # Пропускаем его

            if slot_str not in booked_times:
                all_slots.append(slot_str)
            current_time += timedelta(minutes=30)
        
        print(f"[{datetime.now()}] get_booked_times: Возвращено {len(all_slots)} свободных слотов для стола {table_id} на {date_str}: {all_slots}") # ОТЛАДКА
        return {"status": "ok", "free_times": all_slots}, 200

    except Exception as e:
        logging.error(f"[{datetime.now()}] Ошибка /get_booked_times: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}, 500
        
# =========================
# Основные маршруты Flask
# =========================
@app.route("/")
def index():
    print(f"[{datetime.now()}] Получен GET запрос на /")
    return "Bot is running.", 200

@app.route("/set_webhook_manual")
def set_webhook_manual():
    print(f"[{datetime.now()}] Получен GET запрос на /set_webhook_manual") 
    if not RENDER_EXTERNAL_URL:
        return jsonify({"status": "error", "message": "RENDER_EXTERNAL_URL is not set"}), 500
    if not RENDER_EXTERNAL_URL.startswith("https://"):
        return jsonify({"status": "error", "message": "Webhook requires HTTPS"}), 500
    
    webhook_url = f"{RENDER_EXTERNAL_URL}/webhook"
    try:
        # УДАЛЕНИЕ + УСТАНОВКА
        bot.remove_webhook() 
        print(f"[{datetime.now()}] Старый Webhook удален.") 
        ok = bot.set_webhook(url=webhook_url)
        print(f"[{datetime.now()}] Попытка установки Webhook на {webhook_url}; Результат: {ok}") 
        if ok:
            return jsonify({"status": "ok", "message": f"Webhook set to {webhook_url}"}), 200
        else:
            return jsonify({"status": "error", "message": "Failed to set webhook"}), 500
    except Exception as e:
        print(f"[{datetime.now()}] Ошибка при установке Webhook вручную: {e}") 
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/webhook", methods=["POST"])
def webhook():
    print(f"[{datetime.now()}] Получен POST запрос на /webhook") 
    if request.headers.get("content-type") == "application/json":
        json_string = request.get_data(as_text=True)
        print(f"[{datetime.now()}] Webhook: Получены данные. Длина: {len(json_string)}") 
        try:
            update = types.Update.de_json(json_string)
            print(f"[{datetime.now()}] Webhook: Успешно десериализовано обновление.") 
            
            # СИНХРОННАЯ ОБРАБОТКА - КЛЮЧ К СТАБИЛЬНОСТИ
            bot.process_new_updates([update])
            
            print(f"[{datetime.now()}] Webhook: Возвращен 200 OK после синхронной обработки.") 
            return "OK", 200
        except Exception as e:
            # Логирование критической ошибки, если process_new_updates терпит крах
            print(f"[{datetime.now()}] Webhook: ОШИБКА обработки обновления: {e}") 
            return "Error processing update", 500 
    else:
        print(f"[{datetime.now()}] Webhook: Неверный тип контента.") 
        return "Invalid content type", 403

# =========================
# CATCH-ALL HANDLER ДЛЯ ДЕБАГА (ДОЛЖЕН БЫТЬ ПОСЛЕДНИМ)
# =========================
@bot.message_handler(func=lambda m: True)
def default_handler(message: types.Message):
    user_id = message.from_user.id
    user_name = message.from_user.full_name or "Неизвестный"
    
    print(f"[{datetime.now()}] (Обработчик) ===> DEFAULT HANDLER HIT! Chat ID: {message.chat.id}, Content Type: '{message.content_type}', Text: '{message.text}'")
    
    if message.text:
        # Проверяем, не является ли это сообщение текстом с клавиатуры, который почему-то не сработал
        if any(keyword in message.text for keyword in ["Моя бронь", "Меню", "Управление", "История", "Забронировать"]):
            print(f"[{datetime.now()}] (Обработчик) ===> Сообщение '{message.text}' - это кнопка, но она не была обработана соответствующим хендлером. Проблема в порядке хендлеров или точности текста.")
            return 
        
        # Отправка заглушки с возвратом клавиатуры
        try:
            bot.send_message(
                message.chat.id, 
                "Извините, я понимаю только команды из меню или `/start`.",
                reply_markup=main_reply_kb(user_id, user_name)
            )
            print(f"[{datetime.now()}] (Обработчик) ===> Отправлен ответ-заглушка пользователю {user_id}")
        except Exception as e:
            print(f"[{datetime.now()}] (Обработчик) Ошибка отправки ответа в default_handler: {e}")


# =========================
# MAIN
# =========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"[{datetime.now()}] Запуск Flask-приложения на порту {port}") 
    
    app.run(host="0.0.0.0", port=port)
