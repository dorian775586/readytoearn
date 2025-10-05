import os
import logging
from datetime import datetime, timedelta, date

from flask import Flask, request, jsonify
from telebot import TeleBot, types
import psycopg2
from psycopg2.extras import RealDictCursor
from flask_cors import CORS

# Настройка логирования
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

# Очистка и нормализация URL/токенов
if RENDER_EXTERNAL_URL:
    RENDER_EXTERNAL_URL = RENDER_EXTERNAL_URL.strip()
if WEBAPP_URL:
    WEBAPP_URL = WEBAPP_URL.strip()

# Принудительное добавление порта для некоторых хостингов PostgreSQL
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
    # Используем RealDictCursor для возврата словарей
    return psycopg2.connect(
        DATABASE_URL, 
        cursor_factory=RealDictCursor,
        sslmode='require' 
    )

def init_db():
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
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
                cur.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS user_id BIGINT;")
                cur.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS user_name TEXT;")
                cur.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS phone TEXT;")
                cur.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS guests INT;")
                cur.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS booking_for TIMESTAMP;")

                cur.execute("SELECT COUNT(*) AS c FROM tables;")
                c = cur.fetchone()["c"]
                if c == 0:
                    # Вставляем столы с ID 1 по 10
                    cur.execute("INSERT INTO tables (id) SELECT generate_series(1, 10);")

            conn.commit()
        print("База данных: OK")
    except Exception as e:
        print(f"Ошибка инициализации базы: {e}")

# =========================
# HELPERS (UI)
# =========================
def main_reply_kb(user_id: int, user_name: str) -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    
    cleaned_webapp_url = WEBAPP_URL.rstrip('/')
    
    # Формируем URL с параметрами
    web_app_url = (
        f"{cleaned_webapp_url}?user_id={user_id}&user_name={user_name}&bot_url={RENDER_EXTERNAL_URL}"
    )
    
    print(f"DEBUG: WebApp URL, отправляемый в Telegram: {web_app_url}")
    
    row1 = [
        types.KeyboardButton("🦊 Забронировать", web_app=types.WebAppInfo(url=web_app_url)),
        types.KeyboardButton("📋 Моя бронь"),
    ]
    row2 = [types.KeyboardButton("📖 Меню")]
    kb.row(*row1)
    kb.row(*row2)
    if ADMIN_ID and str(user_id) == str(ADMIN_ID):
        kb.row(types.KeyboardButton("🛠 Управление"), types.KeyboardButton("🗂 История"))
    return kb

# =========================
# COMMANDS
# =========================
@bot.message_handler(commands=["start"])
def cmd_start(message: types.Message):
    user_id = message.from_user.id
    user_name = message.from_user.full_name or "Неизвестный"
    
    try:
        bot.send_photo(
            message.chat.id,
            photo="https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcQbh6M8aJwxylo8aI1B-ceUHaiOyEnA425a0A&s",
            caption="<b>Рестобар «Белый Лис»</b> приветствует вас!\nТут вы можете дистанционно забронировать любой понравившийся столик!",
            reply_markup=main_reply_kb(user_id, user_name),
            parse_mode="HTML"
        )
        print(f"*** УСПЕШНО ОТПРАВЛЕНО /START ПОЛЬЗОВАТЕЛЮ {user_id} ***")

    except Exception as e:
        logging.error(f"ОШИБКА В CMD_START для user {user_id}: {e}")
        
        try:
            # Запасной вариант отправки (только текст)
            bot.send_message(
                message.chat.id,
                text="⚠️ Проблема с загрузкой меню. Бот работает.\n\n"
                     "<b>Рестобар «Белый Лис»</b> приветствует вас!\n"
                     "Чтобы забронировать столик, нажмите на кнопку 'Забронировать'.",
                reply_markup=main_reply_kb(user_id, user_name),
                parse_mode="HTML"
            )
            print(f"*** УСПЕШНО ОТПРАВЛЕНО ТЕКСТОВОЕ СООБЩЕНИЕ ПОЛЬЗОВАТЕЛЮ {user_id} ***")
        except Exception as text_e:
            logging.error(f"КРИТИЧЕСКАЯ ОШИБКА ОТПРАВКИ ОТВЕТА ПОЛЬЗОВАТЕЛЮ {user_id}: {text_e}")
            
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

# =========================
# TEXT BUTTONS
# =========================
@bot.message_handler(func=lambda m: m.text == "📋 Моя бронь")
def on_my_booking(message: types.Message):
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT booking_id, table_id, time_slot, booking_for
                    FROM bookings
                    WHERE user_id=%s 
                      AND (booking_for + interval '1 hour') > NOW()
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
            bot.send_photo(
                message.chat.id,
                photo=photo_url
            )
        except Exception as e:
            bot.send_message(message.chat.id, f"Произошла ошибка при загрузке фото: {e}")
            logging.error(f"Ошибка при отправке фото: {e}")


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
                    WHERE (booking_for + interval '1 hour') > NOW()
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
            bot.send_message(message.chat.id, text, reply_markup=kb, parse_mode="HTML")

    except Exception as e:
        bot.send_message(message.chat.id, f"Ошибка админ-панели: {e}")

@bot.message_handler(func=lambda m: m.text == "🗂 История")
def on_history_btn(message: types.Message):
    return cmd_history(message)

# =========================
# INLINE CALLBACKS
# =========================
@bot.callback_query_handler(func=lambda c: c.data.startswith("cancel_"))
def on_cancel_user(call: types.CallbackQuery):
    booking_id = int(call.data.split("_")[1])
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM bookings WHERE booking_id=%s AND user_id=%s;", (booking_id, call.from_user.id))
                conn.commit()
        bot.edit_message_text("Бронь отменена.", chat_id=call.message.chat.id, message_id=call.message.id)
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
            user_id = booking_info.get('user_id')
            
            if user_id:
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

# =========================
# CONTACT & WEB_APP DATA
# =========================
@bot.message_handler(content_types=['web_app_data'])
def on_webapp_data(message: types.Message):
    print("ПРИШЛИ ДАННЫЕ ОТ WEBAPP:", message.web_app_data.data)

# =========================
# BOOKING API (для WebApp / внешних вызовов)
# =========================
@app.route("/book", methods=["POST"])
def book_api():
    try:
        data = request.json
        
        # --- ДОБАВЛЕНО ДЕТАЛЬНОЕ ЛОГИРОВАНИЕ ---
        logging.info(f"API /book received data: {data}")
        # ---------------------------------------
        
        user_id_raw = data.get('user_id')
        user_id = int(user_id_raw) if user_id_raw else 0 
        user_name = data.get('user_name') or 'Неизвестный'
        phone = data.get('phone')
        guests = data.get('guests')
        table_id = data.get('table') 
        time_slot = data.get('time') 
        date_str = data.get('date') 

        # Проверка наличия обязательных полей
        required_fields = {
            'phone': phone, 
            'guests': guests, 
            'table': table_id, 
            'time': time_slot, 
            'date': date_str
        }

        missing_fields = [k for k, v in required_fields.items() if not v]
        
        if missing_fields:
            # --- ЛОГ ОШИБКИ И 400 ОТВЕТ ---
            logging.error(f"API /book is missing fields: {missing_fields}")
            return jsonify({
                "status": "error", 
                "message": f"Не хватает данных для бронирования. Отсутствует: {', '.join(missing_fields)}"
            }), 400

        # Продолжение логики бронирования
        booking_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        booking_datetime = datetime.combine(booking_date, datetime.strptime(time_slot, '%H:%M').time())

        with db_connect() as conn:
            # 1. Проверка на дублирование
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM bookings WHERE table_id = %s AND booking_for::date = %s AND time_slot = %s;",
                    (table_id, booking_date, time_slot)
                )
                existing_booking = cursor.fetchone()
                if existing_booking:
                    return {"status": "error", "message": "Этот стол уже забронирован на это время."}, 409
            
            # 2. Создание брони
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO bookings (user_id, user_name, phone, table_id, time_slot, guests, booked_at, booking_for)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
                    """,
                    (user_id, user_name, phone, table_id, time_slot, guests, datetime.now(), booking_datetime) 
                )
                conn.commit()
                
            # 3. Уведомление пользователя
            if user_id: 
                try:
                    formatted_date = booking_date.strftime("%d.%m.%Y")
                    message_text = f"✅ Ваша бронь успешно оформлена!\n\nСтол: {table_id}\nДата: {formatted_date}\nВремя: {time_slot}"
                    bot.send_message(user_id, message_text)
                except Exception as e:
                    print(f"Не удалось отправить уведомление пользователю {user_id}: {e}")

            # 4. Уведомление админа
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

            return jsonify({"status": "ok", "message": "Бронь успешно создана"}), 200

    except Exception as e:
        logging.error(f"Критическая ошибка /book: {e}")
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route("/get_booked_times", methods=["GET"])
def get_booked_times():
    try:
        table_id = request.args.get('table')
        date_str = request.args.get('date')

        if not all([table_id, date_str]):
            return jsonify({"status": "error", "message": "Не хватает данных (стол или дата)"}), 400

        # Убедимся, что дата корректна
        datetime.strptime(date_str, '%Y-%m-%d')
        
        with db_connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT time_slot FROM bookings 
                    WHERE table_id = %s 
                      AND booking_for::date = %s 
                      AND (booking_for + interval '1 hour') > NOW() 
                    ORDER BY time_slot;
                    """, 
                    (table_id, date_str)
                )
                booked_times = [row['time_slot'] for row in cursor.fetchall()]
        
        return jsonify({"status": "ok", "booked_times": booked_times}), 200

    except ValueError:
        return jsonify({"status": "error", "message": "Неверный формат даты."}), 400
    except Exception as e:
        logging.error(f"Ошибка /get_booked_times: {e}") 
        return jsonify({"status": "error", "message": str(e)}), 400


# 🔥🔥🔥 МАРШРУТ, КОТОРЫЙ РЕШАЕТ ОШИБКУ 404 🔥🔥🔥
@app.route("/get_booked_tables", methods=["GET"])
def get_booked_tables():
    """
    Возвращает список table_id, которые заняты на указанную дату и время.
    """
    try:
        date_str = request.args.get('date')
        time_slot = request.args.get('time')

        if not all([date_str, time_slot]):
            return jsonify({"status": "error", "message": "Не хватает данных (дата или время)"}), 400
        
        # Проверка валидности данных
        datetime.strptime(date_str, '%Y-%m-%d')
        datetime.strptime(time_slot, '%H:%M')

        with db_connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT DISTINCT table_id FROM bookings 
                    WHERE booking_for::date = %s 
                      AND time_slot = %s
                      AND (booking_for + interval '1 hour') > NOW(); 
                    """,
                    (date_str, time_slot)
                )
                
                # Собираем список ID занятых столов
                booked_tables = [str(row['table_id']) for row in cursor.fetchall()]
        
        logging.info(f"API /get_booked_tables: Date={date_str}, Time={time_slot}, Booked={booked_tables}")
        return jsonify({"status": "ok", "booked_tables": booked_tables}), 200

    except ValueError:
        return jsonify({"status": "error", "message": "Неверный формат даты или времени."}), 400
    except Exception as e:
        logging.error(f"Ошибка /get_booked_tables: {e}") 
        return jsonify({"status": "error", "message": "Внутренняя ошибка сервера."}), 500


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
        
        try:
            bot.process_new_updates([update])
            return "OK", 200
        except Exception as e:
            logging.error(f"Ошибка при обработке обновления Telebot: {e}")
            return "OK", 200

    else:
        return "Invalid content type", 403


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    if not RENDER_EXTERNAL_URL:
        raise RuntimeError("Ошибка: RENDER_EXTERNAL_URL пуст или не задан!")
    if not RENDER_EXTERNAL_URL.startswith("https://"):
        raise RuntimeError("Ошибка: Telegram webhook требует HTTPS!")

    try:
        # Устанавливаем Webhook при запуске
        bot.remove_webhook()
        webhook_url = f"{RENDER_EXTERNAL_URL}/webhook"
        ok = bot.set_webhook(url=webhook_url)
        print(f"Webhook set -> {webhook_url} ; ok={ok}")
    except Exception as e:
        print("Ошибка установки webhook:", e)
    
    init_db()
    app.run(host="0.0.0.0", port=port)