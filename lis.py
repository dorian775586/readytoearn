import os
import logging
import requests # Удалим позже, если не нужен
from datetime import datetime, timedelta, date, timezone # Добавлена 'date' и 'timezone'
from flask import Flask, request, jsonify
from telebot import TeleBot, types
import psycopg2
from psycopg2.extras import RealDictCursor
from flask_cors import CORS
from dateutil import tz 

# =========================
# НАСТРОЙКА И КОНСТАНТЫ
# =========================
# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Получение переменных окружения
BOT_TOKEN = (os.environ.get("BOT_TOKEN") or "").strip()
DATABASE_URL = (os.environ.get("DATABASE_URL") or "").strip()
ADMIN_ID_ENV = (os.environ.get("ADMIN_ID") or "").strip()
WEBAPP_URL = (os.environ.get("WEBAPP_URL") or "https://gitrepo-drab.vercel.app").strip()
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL")

# Проверки и исправление URL
if not BOT_TOKEN:
    raise RuntimeError("Ошибка: BOT_TOKEN пуст или не задан!")
if not DATABASE_URL:
    raise RuntimeError("Ошибка: DATABASE_URL не задан!")
if not RENDER_EXTERNAL_URL:
    logger.warning("Предупреждение: RENDER_EXTERNAL_URL не задан! Использование заглушки.")

if "render.com/" in DATABASE_URL and ":5432" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace(".render.com/", ".render.com:5432/")

ADMIN_ID = None
if ADMIN_ID_ENV:
    try:
        ADMIN_ID = int(ADMIN_ID_ENV)
        logger.info(f"ADMIN_ID установлен: {ADMIN_ID}")
    except ValueError:
        logger.warning(f"Предупреждение: ADMIN_ID ('{ADMIN_ID_ENV}') не является числом.")

# Константы для бота
RESTAURANT_NAME = "Белый Лис"

# =========================
# DB INIT
# =========================
def db_connect():
    """Устанавливает соединение с базой данных."""
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    """Инициализация таблиц и столов."""
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
                # Создание индексов для оптимизации (как в вашем коде)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_bookings_conflict ON bookings (table_id, booking_for);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_bookings_user_active ON bookings (user_id, booking_for DESC);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_bookings_future_time ON bookings (booking_for);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_bookings_booked_at ON bookings (booked_at DESC);")
                
                # Добавление столов до 20
                TARGET_TABLE_COUNT = 20
                cur.execute("SELECT id FROM tables ORDER BY id ASC;")
                existing_table_ids = [row['id'] for row in cur.fetchall()]
                tables_to_add = [i for i in range(1, TARGET_TABLE_COUNT + 1) if i not in existing_table_ids]
                
                if tables_to_add:
                    insert_values = ",".join(f"({i})" for i in tables_to_add)
                    cur.execute(f"INSERT INTO tables (id) VALUES {insert_values};")
                    logger.info(f"База данных: Добавлено {len(tables_to_add)} новых столов (ID: {tables_to_add}).")
                else:
                    logger.info("База данных: Все столы до 20 уже существуют.")
                
            conn.commit()
        logger.info("База данных: Инициализация завершена успешно.")
    except Exception as e:
        logger.error(f"Ошибка инициализации базы: {e}", exc_info=True)

# =========================
# BOT & APP
# =========================
bot = TeleBot(BOT_TOKEN, parse_mode="HTML")
app = Flask(__name__)
CORS(app)

with app.app_context():
    init_db()

# =========================
# HELPERS (UI)
# =========================
def main_reply_kb(user_id: int, user_name: str) -> types.ReplyKeyboardMarkup:
    """Генерирует основную клавиатуру бота."""
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    
    # URL для WebApp должен содержать данные пользователя и ссылку на бота/бэкэнд
    web_app_url = f"{WEBAPP_URL}?user_id={user_id}&user_name={user_name}&bot_url={RENDER_EXTERNAL_URL}"
    
    row1 = [types.KeyboardButton("🗓️ Забронировать", web_app=types.WebAppInfo(url=web_app_url))]
    row2 = [types.KeyboardButton("📋 Моя бронь"), types.KeyboardButton("📖 Меню")]
    
    kb.row(*row1)
    kb.row(*row2)
    
    if ADMIN_ID and str(user_id) == str(ADMIN_ID):
        kb.row(types.KeyboardButton("🛠 Управление"), types.KeyboardButton("🗂 История"))
    return kb

# =========================
# TELEGRAM COMMANDS & BUTTONS
# =========================

@bot.message_handler(commands=["start"])
def cmd_start(message: types.Message):
    """Обработка команды /start."""
    user_id = message.from_user.id
    user_name = message.from_user.full_name or "Неизвестный"
    bot.send_photo(
        message.chat.id,
        photo="https://placehold.co/600x400/3c3/white?text=Restobar+White+Fox", # Заглушка, замените на свое фото
        caption=f"<b>Рестобар «{RESTAURANT_NAME}»</b> приветствует вас!\nТут вы можете дистанционно забронировать любой понравившийся столик!",
        reply_markup=main_reply_kb(user_id, user_name),
        parse_mode="HTML"
    )

@bot.message_handler(func=lambda m: m.text == "📋 Моя бронь")
def on_my_booking(message: types.Message):
    """Отображение активной брони пользователя с кнопкой отмены."""
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                # Ищем самую последнюю активную бронь (booking_for > NOW())
                cur.execute("""
                    SELECT booking_id, table_id, time_slot, booking_for, phone, guests
                    FROM bookings
                    WHERE user_id=%s AND booking_for > NOW()
                    ORDER BY booking_for ASC
                    LIMIT 1;
                """, (message.from_user.id,))
                row = cur.fetchone()
        
        user_id = message.from_user.id
        user_name = message.from_user.full_name or "Неизвестный"

        if not row:
            bot.send_message(message.chat.id, "У вас нет активной брони.", reply_markup=main_reply_kb(user_id, user_name))
            return
        
        # Преобразование даты в локальный формат для пользователя
        booking_date = row['booking_for'].strftime("%d.%m.%Y")
        
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton(text="❌ Отменить бронь", callback_data=f"cancel_{row['booking_id']}"))
        
        message_text = (
            f"🔖 Ваша активная бронь:\n"
            f"Стол: <b>{row['table_id']}</b>\n"
            f"Дата: <b>{booking_date}</b>\n"
            f"Время: <b>{row['time_slot']}</b>\n"
            f"Гостей: {row.get('guests', 'N/A')}\n"
            f"Телефон: {row.get('phone', 'Не указан')}"
        )
        
        bot.send_message(message.chat.id, 
                         message_text, 
                         parse_mode="HTML",
                         reply_markup=kb)
    except Exception as e:
        logger.error(f"Ошибка в on_my_booking: {e}", exc_info=True)
        bot.send_message(message.chat.id, "Ошибка при получении брони. Попробуйте позже.")


@bot.message_handler(func=lambda m: m.text == "📖 Меню")
def on_menu(message: types.Message):
    """Обработчик кнопки Меню."""
    # Используем заглушки для фото
    menu_photos = [
        "https://placehold.co/400x600/333/white?text=Menu+Page+1", 
        "https://placehold.co/400x600/333/white?text=Menu+Page+2",
        "https://placehold.co/400x600/333/white?text=Menu+Page+3"
    ]
    bot.send_message(message.chat.id, "Загружаю меню, подождите...")

    for photo_url in menu_photos:
        try:
            bot.send_photo(message.chat.id, photo=photo_url)
        except Exception as e:
            bot.send_message(message.chat.id, f"Произошла ошибка при загрузке фото.")
            logger.error(f"Ошибка при отправке фото: {e}")

@bot.message_handler(func=lambda m: m.text == "🛠 Управление" or m.text == "🗂 История")
def on_admin_panel_or_history(message: types.Message):
    """Обработчик админ-кнопок."""
    if not ADMIN_ID or str(message.chat.id) != str(ADMIN_ID):
        bot.send_message(message.chat.id, "У вас нет прав для этой команды.")
        return
    
    if message.text == "🗂 История":
        return cmd_history(message) # Используем существующий обработчик истории
    
    # 🛠 Управление: отображение активных броней.
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT booking_id, user_name, user_id, table_id, time_slot, booking_for, phone, guests
                    FROM bookings
                    WHERE booking_for > NOW()
                    ORDER BY booking_for ASC;
                """)
                rows = cur.fetchall()
        if not rows:
            bot.send_message(message.chat.id, "Активных бронирований нет.")
            return
        
        bot.send_message(message.chat.id, "<b>Активные брони:</b>", parse_mode="HTML")
        
        for r in rows:
            booking_date = r['booking_for'].strftime("%d.%m.%Y")
            # Используем user_id для создания кликабельной ссылки
            user_link = f'<a href="tg://user?id={r["user_id"]}">{r["user_name"]}</a>' if r["user_id"] else r["user_name"]
            
            text = f"🔖 Бронь #{r['booking_id']} — Пользователь: {user_link}\n"
            text += f"   - Стол: <b>{r['table_id']}</b>\n"
            text += f"   - Время: <b>{r['time_slot']} ({booking_date})</b>\n"
            text += f"   - Гостей: {r.get('guests', 'N/A')}\n"
            text += f"   - Телефон: {r.get('phone', 'Не указан')}\n"
            
            kb = types.InlineKeyboardMarkup()
            # Callback для отмены администратором
            kb.add(types.InlineKeyboardButton(text="❌ Отменить", callback_data=f"admin_cancel_{r['booking_id']}"))
            bot.send_message(message.chat.id, text, parse_mode="HTML", reply_markup=kb)

    except Exception as e:
        logger.error(f"Ошибка админ-панели: {e}", exc_info=True)
        bot.send_message(message.chat.id, "Ошибка при получении списка броней.")

def cmd_history(message: types.Message):
    """Обработчик команды /history (доступно только админу)."""
    if not ADMIN_ID or str(message.chat.id) != str(ADMIN_ID):
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
        logger.error(f"Ошибка получения истории для админа: {e}", exc_info=True)
        bot.send_message(message.chat.id, f"Ошибка истории: {e}")

# Обработчик отмены пользователем
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
                    SELECT user_id, user_name, table_id, time_slot, booking_for
                    FROM bookings
                    WHERE booking_id=%s AND user_id=%s;
                """, (booking_id, call.from_user.id))
                booking_info = cur.fetchone()
                
                # 2. Удаляем бронирование
                cur.execute("DELETE FROM bookings WHERE booking_id=%s AND user_id=%s;", (booking_id, call.from_user.id))
                rows_deleted = cur.rowcount
                conn.commit()
        
        if rows_deleted > 0:
            bot.edit_message_text("✅ Бронь отменена.", chat_id=call.message.chat.id, message_id=call.message.id)
            
            # 3. Уведомление администратора об отмене пользователем
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
                        f"Время: {booking_info['time_slot']}"
                    )
                    bot.send_message(ADMIN_ID, message_text, parse_mode="HTML")
                except Exception as e:
                    logger.error(f"Не удалось уведомить админа об отмене брони: {e}")

        else:
            bot.answer_callback_query(call.id, "Бронь уже была отменена или не найдена.", show_alert=True)
            
    except Exception as e:
        logger.error(f"Ошибка при отмене брони пользователем: {e}")
        bot.answer_callback_query(call.id, f"Ошибка: {e}", show_alert=True)


# Обработчик отмены администратором (включает уведомление пользователя)
@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_cancel_"))
def on_cancel_admin(call: types.CallbackQuery):
    """Отмена брони администратором с обязательным уведомлением пользователя."""
    booking_id = int(call.data.split("_")[2])
    if not ADMIN_ID or str(call.from_user.id) != str(ADMIN_ID):
        bot.answer_callback_query(call.id, "У вас нет прав для этого действия.", show_alert=True)
        return
    try:
        booking_info = None
        rows_deleted = 0
        with db_connect() as conn:
            with conn.cursor() as cur:
                # 1. Получаем информацию о бронировании ДО удаления
                cur.execute("SELECT user_id, table_id, time_slot, booking_for FROM bookings WHERE booking_id=%s;", (booking_id,))
                booking_info = cur.fetchone()
                
                # 2. Удаляем бронирование
                cur.execute("DELETE FROM bookings WHERE booking_id=%s;", (booking_id,))
                rows_deleted = cur.rowcount
                conn.commit()
        
        if rows_deleted > 0 and booking_info and booking_info['user_id']:
            # --- Уведомление пользователя об отмене админом ---
            user_id = booking_info['user_id']
            booking_date = booking_info['booking_for'].strftime("%d.%m.%Y")
            message_text = (
                f"❌ <b>ВНИМАНИЕ: Ваша бронь отменена администратором.</b>\n\n"
                f"К сожалению, бронирование на:\n"
                f"Стол: <b>{booking_info['table_id']}</b>\n"
                f"Дата: <b>{booking_date}</b>\n"
                f"Время: <b>{booking_info['time_slot']}</b>\n\n"
                f"было отменено. Приносим свои извинения."
            )
            try:
                # Отправляем уведомление пользователю
                bot.send_message(user_id, message_text, parse_mode="HTML")
            except Exception as e:
                logger.error(f"Не удалось уведомить пользователя {user_id} об отмене брони: {e}")

        bot.edit_message_text(f"✅ Бронь #{booking_id} успешно отменена.", 
                              chat_id=call.message.chat.id, 
                              message_id=call.message.id,
                              reply_markup=None) # Удаляем inline-кнопку
        bot.answer_callback_query(call.id, "Бронь отменена.", show_alert=False)
    except Exception as e:
        logger.error(f"Ошибка при отмене брони админом: {e}", exc_info=True)
        bot.answer_callback_query(call.id, f"Ошибка: {e}", show_alert=True)

# =========================
# FLASK API
# =========================

@app.route("/get_booked_times", methods=["GET"])
def get_booked_times():
    """
    Возвращает список свободных слотов для стола на заданную дату.
    Критически важно: фильтрация по времени происходит ТОЛЬКО на бэкэнде (UTC).
    """
    table_id = request.args.get("table", type=int)
    date_str = request.args.get("date")

    if not table_id or not date_str:
        return jsonify({"status": "error", "message": "Не указан стол или дата."}), 400

    try:
        query_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"status": "error", "message": "Неверный формат даты."}), 400

    # Определение всех возможных слотов (примерно с 10:00 до 23:30)
    ALL_SLOTS = []
    for h in range(10, 24):
        for m in [0, 30]:
            if h == 23 and m > 0: continue # Ограничимся 23:00
            ALL_SLOTS.append(f"{h:02d}:{m:02d}")

    now_utc = datetime.utcnow().replace(tzinfo=tz.tzutc())
    today_utc = now_utc.date()
    current_time_str = now_utc.strftime("%H:%M")

    booked_slots = set()
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                # Получаем все занятые слоты для этого стола и даты
                cur.execute("""
                    SELECT time_slot, booking_for FROM bookings
                    WHERE table_id = %s 
                    AND DATE(booking_for) = %s 
                    ORDER BY time_slot ASC;
                """, (table_id, query_date))
                
                rows = cur.fetchall()
                booked_slots = {row['time_slot'] for row in rows}

    except Exception as e:
        logger.error(f"Ошибка БД при получении времени: {e}")
        return jsonify({"status": "error", "message": "Ошибка базы данных."}), 500

    free_slots = []
    
    # Фильтруем: не должно быть забронировано И должно быть в будущем (если это сегодня по UTC)
    for slot in ALL_SLOTS:
        if slot not in booked_slots:
            is_future_slot = True
            
            # Если запрошенная дата - СЕГОДНЯ (по UTC), отфильтровываем прошедшее время
            if query_date == today_utc:
                if slot < current_time_str:
                    is_future_slot = False
            
            if is_future_slot:
                free_slots.append(slot)
    
    logger.info(f"get_booked_times: Возвращено {len(free_slots)} свободных слотов для стола {table_id} на {date_str}")
    
    return jsonify({
        "status": "ok", 
        "table_id": table_id,
        "date": date_str,
        "free_times": free_slots
    })

@app.route("/book", methods=["POST"])
def book_api():
    """Обрабатывает POST-запрос на бронирование, отправляет подтверждение пользователю и уведомление админу."""
    try:
        data = request.json
        user_id = data.get('user_id')
        user_name = data.get('user_name') or 'Неизвестный'
        table_id = data.get('table_id')
        time_slot = data.get('time_slot')
        date_str = data.get('date_str') 
        phone = data.get('phone', 'Не указан')
        guests = data.get('guests', 1)

        if not all([user_id, table_id, time_slot, date_str]):
            return jsonify({"status": "error", "message": "Недостаточно данных для бронирования."}), 400

        # Формируем полную дату/время бронирования (храним в UTC)
        booking_datetime_str = f"{date_str} {time_slot}"
        try:
            # Парсим как UTC время
            # Используем встроенный timezone.utc для совместимости с postgres
            booking_for = datetime.strptime(booking_datetime_str, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        except ValueError:
            return jsonify({"status": "error", "message": "Неверный формат даты или времени."}), 400

        # Проверка на прошедшее время
        if booking_for < datetime.now(timezone.utc):
            return jsonify({"status": "error", "message": "Нельзя забронировать прошедшее время."}), 400
        
        with db_connect() as conn:
            with conn.cursor() as cur:
                # Проверка конфликтов
                cur.execute("""
                    SELECT booking_id FROM bookings
                    WHERE table_id = %s AND booking_for = %s
                """, (table_id, booking_for))
                if cur.fetchone():
                    return jsonify({"status": "error", "message": "Этот стол уже забронирован на указанное время."}), 409

                # Вставка бронирования
                cur.execute("""
                    INSERT INTO bookings (user_id, user_name, phone, guests, table_id, time_slot, booking_for)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING booking_id;
                """, (user_id, user_name, phone, guests, table_id, time_slot, booking_for))
                
                new_booking_id = cur.fetchone()['booking_id']
                conn.commit()

        # 1. Отправляем пользователю подтверждение
        booking_date_formatted = booking_for.strftime("%d.%m.%Y")
        try:
            user_msg = (
                f"✅ <b>Ваша бронь подтверждена!</b>\n\n"
                f"Стол: <b>{table_id}</b>\n"
                f"Дата: {booking_date_formatted}\n"
                f"Время: {time_slot}\n"
                f"Гостей: {guests}\n"
                f"Телефон: {phone}"
            )
            bot.send_message(user_id, user_msg, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Не удалось отправить подтверждение пользователю {user_id}: {e}")

        # 2. Уведомление администратора о новой брони
        if ADMIN_ID:
            user_link = f'<a href="tg://user?id={user_id}">{user_name}</a>'
            admin_msg = (
                f"📩 <b>НОВАЯ БРОНЬ: #{new_booking_id}</b>\n"
                f"Пользователь: {user_link}\n"
                f"Стол: <b>{table_id}</b>\n"
                f"Дата/Время: <b>{booking_date_formatted} в {time_slot}</b>\n"
                f"Гостей: {guests}\n"
                f"Телефон: {phone}"
            )
            try:
                bot.send_message(ADMIN_ID, admin_msg, parse_mode="HTML")
            except Exception as e:
                logger.error(f"Не удалось уведомить админа о новой брони: {e}")

        return jsonify({"status": "ok", "message": "Бронь успешно создана.", "booking_id": new_booking_id}), 201

    except Exception as e:
        logger.error(f"Ошибка в book_api: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/")
def index():
    """Базовый маршрут для проверки статуса."""
    return "Bot is running.", 200

# =========================
# TELEGRAM WEBHOOK / SERVER START
# =========================
@app.route("/webhook", methods=['POST'])
def get_message():
    """Обработка вебхука Telegram."""
    json_string = request.get_data().decode('utf-8')
    update = types.Update.de_json(json_string)
    bot.process_new_updates([update])
    return "!", 200

if __name__ == '__main__':
    if RENDER_EXTERNAL_URL:
        # Установка вебхука при старте
        bot.remove_webhook()
        bot.set_webhook(url=RENDER_EXTERNAL_URL + "/webhook")
        logger.info(f"Вебхук установлен на: {RENDER_EXTERNAL_URL}/webhook")
    
    app.run(host="0.0.0.0", port=int(os.environ.get('PORT', 5000)))
