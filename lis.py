import os
import logging
from datetime import datetime, timedelta, date

from flask import Flask, request, jsonify
from telebot import TeleBot, types
import psycopg2
from psycopg2.extras import RealDictCursor
from flask_cors import CORS

# =========================
# ЛОГИРОВАНИЕ
# =========================
logging.basicConfig(level=logging.INFO)

# =========================
# ENV
# =========================
BOT_TOKEN = (os.environ.get("BOT_TOKEN") or "").strip()
DATABASE_URL = (os.environ.get("DATABASE_URL") or "").strip()
ADMIN_ID_ENV = (os.environ.get("ADMIN_ID") or "").strip()
# Убедитесь, что эта ссылка актуальна для вашего проекта на Vercel
WEBAPP_URL = (os.environ.get("WEBAPP_URL") or "https://gitrepo-drab.vercel.app").strip() 
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL")

if not BOT_TOKEN:
    raise RuntimeError("Ошибка: BOT_TOKEN пуст или не задан!")
if not DATABASE_URL:
    raise RuntimeError("Ошибка: DATABASE_URL не задан!")
if not RENDER_EXTERNAL_URL:
    raise RuntimeError("Ошибка: RENDER_EXTERNAL_URL не задан! Проверьте переменные окружения на Render.")

if "render.com/" in DATABASE_URL and ":5432" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace(".render.com/", ".render.com:5432/")

ADMIN_ID = None
if ADMIN_ID_ENV:
    try:
        ADMIN_ID = int(ADMIN_ID_ENV)
    except ValueError:
        print(f"Предупреждение: ADMIN_ID ('{ADMIN_ID_ENV}') не является числом; админ-функции отключены.")

# =========================
# КОНСТАНТЫ МЕНЮ (ИЗМЕНЕНО ДЛЯ ИНТЕРАКТИВНОГО МЕНЮ)
# =========================
RESTAURANT_NAME = "Белый Лис"
# Базовый URL для ваших изображений. ОБНОВИТЕ, если ваш домен изменился.
BASE_MENU_IMAGE_URL = "https://gitrepo-drab.vercel.app/images" 

MENU_CATEGORIES = {
    "🥣 Закуски (Холодные)": f"{BASE_MENU_IMAGE_URL}/menu1.jpg", # Проверьте путь
    "🌶️ Закуски (Горячие/Супы)": f"{BASE_MENU_IMAGE_URL}/menu2.jpg", # Проверьте путь
    "🥗 Салаты": f"{BASE_MENU_IMAGE_URL}/menu3.jpg",
    "🍔 Бургеры": f"{BASE_MENU_IMAGE_URL}/menu4.jpg",
    "🌯 Сэндвичи & Роллы": f"{BASE_MENU_IMAGE_URL}/menu5.jpg",
    "🍖 Основное (Говядина)": f"{BASE_MENU_IMAGE_URL}/menu6.jpg",
    "🐟 Основное (Рыба/Свинина)": f"{BASE_MENU_IMAGE_URL}/menu7.jpg",
    "🍗 Основное (Курица/Утка)": f"{BASE_MENU_IMAGE_URL}/menu8.jpg",
    "🥩 Премиум Стейки": f"{BASE_MENU_IMAGE_URL}/menu9.jpg",
    "☕ Десерты & Напитки": f"{BASE_MENU_IMAGE_URL}/menu10.jpg",
}
# Актуальный URL для приветственного фото
WELCOME_PHOTO_URL = "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcQbh6M8aJwxylo8aI1B-ceUHaiOyEnA425a0A&s" 

# =========================
# DB INIT
# =========================
def db_connect():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                # Таблицы
                cur.execute("""
                CREATE TABLE IF NOT EXISTS tables (
                    id INT PRIMARY KEY
                );
                """)
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
                # Добавляем колонки на всякий случай
                cur.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS user_id BIGINT;")
                cur.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS user_name TEXT;")
                cur.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS phone TEXT;")
                cur.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS guests INT;")
                cur.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS booking_for TIMESTAMP;")
                
                # ========================================================
                # ДОБАВЛЕНИЕ ИНДЕКСОВ ДЛЯ ОПТИМИЗАЦИИ
                # ========================================================
                # 1. Композитный индекс для быстрой проверки конфликтов (table_id, date, time)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_bookings_conflict ON bookings (table_id, booking_for);")
                
                # 2. Индекс для быстрого поиска активной брони пользователя (Моя бронь)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_bookings_user_active ON bookings (user_id, booking_for DESC);")
                
                # 3. Индекс для админ-панели и общей истории (сортировка и поиск)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_bookings_future_time ON bookings (booking_for);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_bookings_booked_at ON bookings (booked_at DESC);")
                # ========================================================

                # ========================================================
                # ИЗМЕНЕНИЕ: Расширяем количество столов до 20,
                #           добавляя только недостающие.
                # ========================================================
                TARGET_TABLE_COUNT = 20
                cur.execute("SELECT id FROM tables ORDER BY id ASC;")
                existing_table_ids = [row['id'] for row in cur.fetchall()]
                tables_to_add = [i for i in range(1, TARGET_TABLE_COUNT + 1) if i not in existing_table_ids]
                
                if tables_to_add:
                    # Создаем строку для множественной вставки: (1), (2), (3)...
                    insert_values = ",".join(f"({i})" for i in tables_to_add)
                    cur.execute(f"INSERT INTO tables (id) VALUES {insert_values};")
                    print(f"База данных: Добавлено {len(tables_to_add)} новых столов (ID: {tables_to_add}).")
                else:
                    print("База данных: Все столы до 20 уже существуют.")
                # ========================================================

            conn.commit()
        print("База данных: OK")
    except Exception as e:
        print(f"Ошибка инициализации базы: {e}")

# =========================
# BOT & APP
# =========================
bot = TeleBot(BOT_TOKEN, parse_mode="HTML")
app = Flask(__name__)
CORS(app)

# ВЫЗОВ ИНИЦИАЛИЗАЦИИ БД - ТЕПЕРЬ ПОСЛЕ ОПРЕДЕЛЕНИЯ init_db()
with app.app_context():
    init_db()

# =========================
# HELPERS (UI)
# =========================
def main_reply_kb(user_id: int, user_name: str) -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    
    # ВОССТАНОВЛЕНА КНОПКА WEBAPP
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
# =========================
@bot.message_handler(commands=["start"])
def cmd_start(message: types.Message):
    user_id = message.from_user.id
    user_name = message.from_user.full_name or "Неизвестный"
    bot.send_photo(
        message.chat.id,
        photo=WELCOME_PHOTO_URL, # ИСПОЛЬЗУЕМ КОНСТАНТУ
        caption=f"<b>Рестобар «{RESTAURANT_NAME}»</b> приветствует вас!\nТут вы можете дистанционно забронировать любой понравившийся столик!",
        reply_markup=main_reply_kb(user_id, user_name),
        parse_mode="HTML"
    )

@bot.message_handler(commands=["history"])
def cmd_history(message: types.Message):
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
        bot.send_message(message.chat.id, text)
    except Exception as e:
        bot.send_message(message.chat.id, f"Ошибка истории: {e}")

@bot.message_handler(func=lambda m: m.text == "📋 Моя бронь")
def on_my_booking(message: types.Message):
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
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

# --- НОВОЕ ИНТЕРАКТИВНОЕ МЕНЮ ---
@bot.message_handler(func=lambda m: m.text == "📖 Меню")
def on_menu(message: types.Message):
    """Отправляет клавиатуру с категориями меню."""
    kb = types.InlineKeyboardMarkup(row_width=2) 
    
    buttons = []
    for name in MENU_CATEGORIES.keys():
        buttons.append(types.InlineKeyboardButton(name, callback_data=f"menu_cat_{name}"))
        
    kb.add(*buttons)
    
    bot.send_message(
        message.chat.id, 
        "🍽️ Выберите интересующий вас раздел меню:",
        reply_markup=kb
    )
# -----------------------------------

# =========================
# АДМИН-ПАНЕЛЬ
# =========================
@bot.message_handler(func=lambda m: m.text == "🛠 Управление")
def on_admin_panel(message: types.Message):
    if not ADMIN_ID or str(message.chat.id) != str(ADMIN_ID):
        bot.send_message(message.chat.id, "У вас нет прав для этой команды.")
        return
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
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
            text += f"   - Стол: {r['table_id']}\n"
            text += f"   - Время: {r['time_slot']} ({booking_date})\n"
            text += f"   - Телефон: {r['phone']}\n"
            
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton(text="❌ Отменить", callback_data=f"admin_cancel_{r['booking_id']}"))
            bot.send_message(message.chat.id, text, parse_mode="HTML", reply_markup=kb)

    except Exception as e:
        bot.send_message(message.chat.id, f"Ошибка админ-панели: {e}")

@bot.message_handler(func=lambda m: m.text == "🗂 История")
def on_history_btn(message: types.Message):
    return cmd_history(message)

# =========================
# CALLBACKS
# =========================

# --- НОВАЯ ФУНКЦИЯ: Обработчик категорий меню ---
@bot.callback_query_handler(func=lambda c: c.data.startswith("menu_cat_"))
def on_menu_category_select(call: types.CallbackQuery):
    """Обрабатывает выбор категории меню и отправляет соответствующее фото."""
    
    category_name = call.data.split("menu_cat_")[1]
    photo_url = MENU_CATEGORIES.get(category_name)
    
    # Создаем клавиатуру с кнопкой "Назад" или со всеми категориями
    kb = types.InlineKeyboardMarkup(row_width=2)
    buttons = [types.InlineKeyboardButton(name, callback_data=f"menu_cat_{name}") for name in MENU_CATEGORIES.keys()]
    kb.add(*buttons)
    
    if photo_url:
        try:
            bot.send_photo(
                call.message.chat.id, 
                photo=photo_url,
                caption=f"Раздел: <b>{category_name}</b>",
                parse_mode="HTML"
            )
            
            # Отправляем сообщение с клавиатурой для выбора следующего раздела
            bot.send_message(
                call.message.chat.id, 
                "⬇️ Выберите следующий раздел:",
                reply_markup=kb
            )

            bot.answer_callback_query(call.id, text=f"Открываю: {category_name}")
            
        except Exception as e:
            logging.error(f"Ошибка при отправке фото меню ({photo_url}): {e}")
            bot.send_message(call.message.chat.id, f"Произошла ошибка при загрузке раздела <b>{category_name}</b>. Проверьте URL изображения.", parse_mode="HTML")
            bot.answer_callback_query(call.id, text="Ошибка загрузки.", show_alert=True)
            
    else:
        bot.answer_callback_query(call.id, text="Раздел не найден.", show_alert=True)
# --------------------------------------------------


@bot.callback_query_handler(func=lambda c: c.data.startswith("cancel_"))
def on_cancel_user(call: types.CallbackQuery):
    booking_id = int(call.data.split("_")[1])
    try:
        booking_info = None
        rows_deleted = 0
        
        with db_connect() as conn:
            with conn.cursor() as cur:
                # 1. Получаем информацию о бронировании ДО удаления
                cur.execute("""
                    SELECT user_id, user_name, table_id, time_slot, booking_for, phone, guests
                    FROM bookings
                    WHERE booking_id=%s AND user_id=%s;
                """, (booking_id, call.from_user.id))
                booking_info = cur.fetchone()
                
                # 2. Удаляем бронирование
                cur.execute("DELETE FROM bookings WHERE booking_id=%s AND user_id=%s;", (booking_id, call.from_user.id))
                rows_deleted = cur.rowcount
                conn.commit()
        
        if rows_deleted > 0:
            bot.edit_message_text("Бронь отменена.", chat_id=call.message.chat.id, message_id=call.message.id)
            
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
                except Exception as e:
                    print(f"Не удалось уведомить админа об отмене брони: {e}")

        else:
            # Если 0 строк удалено (бронь уже отменена/не найдена)
            bot.answer_callback_query(call.id, "Бронь уже была отменена или не найдена.", show_alert=True)
            
    except Exception as e:
        bot.answer_callback_query(call.id, f"Ошибка: {e}", show_alert=True)


@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_cancel_"))
def on_cancel_admin(call: types.CallbackQuery):
    booking_id = int(call.data.split("_")[2])
    if not ADMIN_ID or str(call.from_user.id) != str(ADMIN_ID):
        bot.answer_callback_query(call.id, "У вас нет прав для этого действия.", show_alert=True)
        return
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                booking_info = None
                cur.execute("SELECT user_id, table_id, time_slot, booking_for FROM bookings WHERE booking_id=%s;", (booking_id,))
                booking_info = cur.fetchone()

                cur.execute("DELETE FROM bookings WHERE booking_id=%s;", (booking_id,))
                conn.commit()
        
        if booking_info:
            user_id = booking_info['user_id']
            booking_date = booking_info['booking_for'].strftime("%d.%m.%Y")
            message_text = f"❌ Ваша бронь отменена администратором.\n\nСтол: {booking_info['table_id']}\nДата: {booking_date}\nВремя: {booking_info['time_slot']}"
            try:
                bot.send_message(user_id, message_text)
            except Exception as e:
                print(f"Не удалось уведомить пользователя {user_id} об отмене брони: {e}")

        bot.edit_message_text(f"Бронь #{booking_id} успешно отменена.", chat_id=call.message.chat.id, message_id=call.message.id)
        bot.answer_callback_query(call.id, "Бронь отменена.", show_alert=True)
    except Exception as e:
        bot.answer_callback_query(call.id, f"Ошибка: {e}", show_alert=True)

@bot.message_handler(content_types=['web_app_data'])
def on_webapp_data(message: types.Message):
    print("ПРИШЛИ ДАННЫЕ ОТ WEBAPP:", message.web_app_data.data)

# =========================
# BOOKING API
# =========================
@app.route("/book", methods=["POST"])
def book_api():
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
            return {"status": "error", "message": "Не хватает данных для бронирования"}, 400

        booking_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        booking_datetime = datetime.combine(booking_date, datetime.strptime(time_slot, '%H:%M').time())

        conn = psycopg2.connect(DATABASE_URL)

        with conn.cursor() as cursor:
            # ПРОВЕРКА НА ДУБЛИКАТ
            cursor.execute(
                "SELECT 1 FROM bookings WHERE table_id = %s AND booking_for::date = %s AND time_slot = %s;",
                (table_id, booking_date, time_slot)
            )
            existing_booking = cursor.fetchone()
            if existing_booking:
                return {"status": "error", "message": "Этот стол уже забронирован на это время."}, 409
        
        with conn.cursor() as cursor:
            # СОЗДАНИЕ БРОНИ
            cursor.execute(
                """
                INSERT INTO bookings (user_id, user_name, phone, table_id, time_slot, guests, booked_at, booking_for)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
                """,
                (user_id, user_name, phone, table_id, time_slot, guests, datetime.now(), booking_datetime)
            )
            conn.commit()
            
        # уведомления пользователю
        try:
            formatted_date = booking_date.strftime("%d.%m.%Y")
            message_text = f"✅ Ваша бронь успешно оформлена!\n\nСтол: {table_id}\nДата: {formatted_date}\nВремя: {time_slot}"
            bot.send_message(user_id, message_text)
        except Exception as e:
            print(f"Не удалось отправить уведомление пользователю {user_id}: {e}")

        # уведомление админу
        if ADMIN_ID:
            try:
                formatted_date = booking_date.strftime("%d.%m.%Y")
                user_link = f'<a href="tg://user?id={user_id}">{user_name}</a>' if user_id else user_name
                message_text = (
                    f"Новая бронь:\n"
                    f"Пользователь: {user_link}\n"
                    f"Стол: {table_id}\n"
                    f"Дата: {formatted_date}\n"
                    f"Время: {time_slot}\n"
                    f"Гостей: {guests}\n"
                    f"Телефон: {phone}"
                )
                bot.send_message(ADMIN_ID, message_text, parse_mode="HTML")
            except Exception as e:
                print("Не удалось отправить сообщение админу:", e)

        return {"status": "ok", "message": "Бронь успешно создана"}, 200

    except Exception as e:
        logging.error(f"Ошибка /book: {e}")
        return {"status": "error", "message": str(e)}, 400

# =========================
# GET BOOKED TIMES (с проверкой занятых слотов)
# =========================
@app.route("/get_booked_times", methods=["GET"])
def get_booked_times():
    try:
        table_id = request.args.get('table')
        date_str = request.args.get('date')

        if not all([table_id, date_str]):
            return {"status": "error", "message": "Не хватает данных (стол или дата)"}, 400

        try:
            query_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return {"status": "error", "message": "Неверный формат даты. Ожидается YYYY-MM-DD."}, 400

        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT time_slot FROM bookings WHERE table_id = %s AND booking_for::date = %s;",
                (table_id, query_date)
            )
            booked_times = [row['time_slot'] for row in cursor.fetchall()]

        # генерация всех слотов с 12:00 до 23:00 (как в вашем коде)
        start_time = datetime.combine(query_date, datetime.strptime("12:00", "%H:%M").time())
        end_time = datetime.combine(query_date, datetime.strptime("23:00", "%H:%M").time())
        current_time = start_time
        all_slots = []
        while current_time <= end_time:
            slot_str = current_time.strftime("%H:%M")
            if slot_str not in booked_times:
                all_slots.append(slot_str)
            current_time += timedelta(minutes=30)

        return {"status": "ok", "free_times": all_slots}, 200

    except Exception as e:
        logging.error(f"Ошибка /get_booked_times: {e}")
        return {"status": "error", "message": str(e)}, 500

# =========================
# Основные маршруты
# =========================
@app.route("/")
def index():
    return "Bot is running.", 200

@app.route("/set_webhook_manual")
def set_webhook_manual():
    if not RENDER_EXTERNAL_URL:
        return jsonify({"status": "error", "message": "RENDER_EXTERNAL_URL is not set"}), 500
    if not RENDER_EXTERNAL_URL.startswith("https://"):
        return jsonify({"status": "error", "message": "Webhook requires HTTPS"}), 500
    
    webhook_url = f"{RENDER_EXTERNAL_URL}/webhook"
    try:
        ok = bot.set_webhook(url=webhook_url)
        if ok:
            return jsonify({"status": "ok", "message": f"Webhook set to {webhook_url}"}), 200
        else:
            return jsonify({"status": "error", "message": "Failed to set webhook"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/webhook", methods=["POST"])
def webhook():
    if request.headers.get("content-type") == "application/json":
        json_string = request.get_data(as_text=True)
        update = types.Update.de_json(json_string)
        # Обработка без threading, как в вашей версии
        bot.process_new_updates([update]) 
        return "OK", 200
    else:
        return "Invalid content type", 403

# =========================
# MAIN
# =========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    if not RENDER_EXTERNAL_URL:
        raise RuntimeError("Ошибка: RENDER_EXTERNAL_URL пуст или не задан!")
    if not RENDER_EXTERNAL_URL.startswith("https://"):
        raise RuntimeError("Ошибка: Telegram webhook требует HTTPS!")

    try:
        bot.remove_webhook()
        webhook_url = f"{RENDER_EXTERNAL_URL}/webhook"
        ok = bot.set_webhook(url=webhook_url)
        print(f"Webhook set -> {webhook_url} ; ok={ok}")
    except Exception as e:
        print("Ошибка установки webhook:", e)
    
    app.run(host="0.0.0.0", port=port)