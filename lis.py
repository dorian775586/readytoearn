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
# BOT & APP
# =========================
bot = TeleBot(BOT_TOKEN, parse_mode="HTML")
app = Flask(__name__)
CORS(app)

# =========================
# DB INIT
# =========================
def db_connect():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                # =========================
                # ИЗМЕНЕНИЯ: ДОБАВЛЕНА ПОДДЕРЖКА ЗАЛОВ
                # =========================
                # 1. ТАБЛИЦЫ
                cur.execute("DROP TABLE IF EXISTS tables CASCADE;")
                cur.execute("""
                CREATE TABLE tables (
                    id INT PRIMARY KEY,
                    hall_name VARCHAR(50) NOT NULL
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
                # ДОБАВЛЕНИЕ ИНДЕКСОВ ДЛЯ ОПТИМИЗАЦИИ (без изменений)
                # ========================================================
                cur.execute("CREATE INDEX IF NOT EXISTS idx_bookings_conflict ON bookings (table_id, booking_for);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_bookings_user_active ON bookings (user_id, booking_for DESC);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_bookings_future_time ON bookings (booking_for);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_bookings_booked_at ON bookings (booked_at DESC);")
                # ========================================================

                # 2. ЗАПОЛНЕНИЕ СТОЛОВ ПО ЗАЛАМ (6 основных, 4 терраса - всего 10)
                cur.execute("SELECT COUNT(*) AS c FROM tables;")
                c = cur.fetchone()["c"]
                if c == 0:
                    # Основной зал (id 1-6)
                    for i in range(1, 7):
                        cur.execute("INSERT INTO tables (id, hall_name) VALUES (%s, 'Основной зал');", (i,))
                    # Терраса (id 7-10)
                    for i in range(7, 11):
                        cur.execute("INSERT INTO tables (id, hall_name) VALUES (%s, 'Терраса');", (i,))
                    print("Таблицы: 10 столов распределены по залам.")
                
            conn.commit()
        print("База данных: OK")
    except Exception as e:
        print(f"Ошибка инициализации базы: {e}")

# =========================
# HELPERS (UI)
# =========================
def main_reply_kb(user_id: int, user_name: str) -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    
    # ИЗМЕНЕНИЕ: ВАША КНОПКА БРОНИРОВАНИЯ
    web_app_url = f"{WEBAPP_URL}?user_id={user_id}&user_name={user_name}&bot_url={RENDER_EXTERNAL_URL}"
    row1 = [
        types.KeyboardButton("✅ Записаться онлайн", web_app=types.WebAppInfo(url=web_app_url)),
    ]
    
    row2 = [
        types.KeyboardButton("📋 Моя бронь"),
        types.KeyboardButton("📖 Меню")
    ]
    
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
        # НОВАЯ ССЫЛКА НА ФОТО «МАМА ХУАНА»
        photo="https://encrypted-tbn0.gstatic.com/images?q=tbn:ANdGcSQEWj37bVRbDfps6Ltix6_DffSVFOFXNzNlg&s",
        
        # НОВЫЙ ТЕКСТ ПРИВЕТСТВИЯ
        caption="<b>Ресторан «Мама Хуана» в Гомеле!</b>\nЗдесь вы можете дистанционно забронировать любой понравившийся столик!",
        
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

@bot.message_handler(func=lambda m: m.text == "📖 Меню")
def on_menu(message: types.Message):
    # ИЗМЕНЕНИЕ: ВАМ НУЖНО ЗАМЕНИТЬ ЭТИ ССЫЛКИ НА МЕНЮ «МАМА ХУАНА»
    menu_photos = [
        "https://gitrepo-drab.vercel.app/images/menu1.jpg",
        "https://gitrepo-drab.vercel.app/images/menu2.jpg",
        "https://gitrepo-drab.vercel.app/images/menu3.jpg",
        "https://gitrepo-drab.vercel.app/images/menu4.jpg",
        "https://gitrepo-drab.vercel.app/images/menu5.jpg",
        "https://gitrepo-drab.vercel.app/images/menu6.jpg"
    ]
    
    bot.send_message(message.chat.id, "Загружаю меню, подождите...")

    for photo_url in menu_photos:
        try:
            bot.send_photo(message.chat.id, photo=photo_url)
        except Exception as e:
            bot.send_message(message.chat.id, f"Произошла ошибка при загрузке фото: {e}")
            logging.error(f"Ошибка при отправке фото: {e}")

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
            text += f"  - Стол: {r['table_id']}\n"
            text += f"  - Время: {r['time_slot']} ({booking_date})\n"
            text += f"  - Телефон: {r['phone']}\n"
            
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
                    # Используем полное имя пользователя из call.from_user.full_name, если user_name в базе пуст
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
        # ИЗМЕНЕНИЯ: ТЕПЕРЬ ПОЛУЧАЕМ ПАРАМЕТР hall
        hall_name = request.args.get('hall')
        date_str = request.args.get('date')

        if not all([hall_name, date_str]):
            return {"status": "error", "message": "Не хватает данных (зал или дата)"}, 400

        try:
            query_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return {"status": "error", "message": "Неверный формат даты. Ожидается YYYY-MM-DD."}, 400

        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

        # 1. НАЙТИ СТОЛЫ, КОТОРЫЕ ОТНОСЯТСЯ К ВЫБРАННОМУ ЗАЛУ
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM tables WHERE hall_name = %s;",
                (hall_name,)
            )
            hall_tables = [row['id'] for row in cursor.fetchall()]
            
            if not hall_tables:
                 return {"status": "ok", "all_tables": [], "booked_slots": {}, "message": "Зал не найден или нет столов"}, 200

        # 2. ПОЛУЧИТЬ ЗАНЯТЫЕ СЛОТЫ ТОЛЬКО ДЛЯ ЭТИХ СТОЛОВ
        booked_times_by_table = {}
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT table_id, time_slot FROM bookings 
                WHERE booking_for::date = %s AND table_id = ANY(%s);
                """,
                (query_date, hall_tables)
            )
            for row in cursor.fetchall():
                if row['table_id'] not in booked_times_by_table:
                    booked_times_by_table[row['table_id']] = []
                booked_times_by_table[row['table_id']].append(row['time_slot'])

        # 3. ФОРМИРОВАНИЕ ОТВЕТА ДЛЯ ФРОНТЕНДА
        response_data = {
            "status": "ok",
            "all_tables": hall_tables, # Все столы в зале
            "booked_slots": booked_times_by_table # Занятые слоты
        }
        return jsonify(response_data), 200

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
    
    init_db()
    app.run(host="0.0.0.0", port=port)