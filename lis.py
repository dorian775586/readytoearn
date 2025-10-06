import os
import asyncio
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
import asyncpg

# Инициализация Flask
app = Flask(__name__)
CORS(app)

# Подключение к PostgreSQL (Render)
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise Exception("❌ DATABASE_URL не найден! Укажи его в переменных Render.")

# Создаём глобальный пул подключений
db_pool = None


async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL, ssl="require")

    async with db_pool.acquire() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS tables (
            id SERIAL PRIMARY KEY
        )
        """)
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            booking_id SERIAL PRIMARY KEY,
            table_id INTEGER NOT NULL,
            time_slot TEXT NOT NULL,
            booked_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            booking_for TIMESTAMP,
            phone TEXT NOT NULL,
            user_id BIGINT,
            user_name VARCHAR(255),
            guests INT
        )
        """)
        # Заполнение столиков, если пусто
        count = await conn.fetchval("SELECT COUNT(*) FROM tables;")
        if count == 0:
            await conn.executemany("INSERT INTO tables (id) VALUES ($1)", [(i,) for i in range(1, 11)])
        print("✅ Таблицы готовы и инициализированы.")


# ------------------------------------------------------------------------------
# 🔹 Эндпоинт: получить занятые тайм-слоты
# ------------------------------------------------------------------------------
@app.route("/get_booked_times", methods=["GET"])
async def get_booked_times():
    table = request.args.get("table")
    date = request.args.get("date")

    if not table or not date:
        return jsonify({"status": "error", "message": "Неверные параметры"}), 400

    try:
        async with db_pool.acquire() as conn:
            records = await conn.fetch("""
                SELECT time_slot FROM bookings
                WHERE table_id = $1
                AND DATE(booking_for) = $2
            """, int(table), date)

            booked_times = [r["time_slot"] for r in records]
            return jsonify({"status": "ok", "booked_times": booked_times})

    except Exception as e:
        print("❌ Ошибка get_booked_times:", e)
        return jsonify({"status": "error", "message": str(e)}), 500


# ------------------------------------------------------------------------------
# 🔹 Эндпоинт: создать бронь
# ------------------------------------------------------------------------------
@app.route("/book", methods=["POST"])
async def book():
    data = await request.get_json(force=True, silent=True)

    required = ["user_id", "user_name", "table", "date", "time", "guests", "phone"]
    if not all(field in data for field in required):
        return jsonify({"status": "error", "message": "Не хватает данных"}), 400

    table_id = int(data["table"])
    date = data["date"]
    time_slot = data["time"]
    phone = data["phone"]
    user_id = int(data["user_id"]) if data.get("user_id") else None
    user_name = data["user_name"]
    guests = int(data["guests"])

    try:
        # Создаём datetime брони
        booking_for = datetime.strptime(f"{date} {time_slot}", "%Y-%m-%d %H:%M")

        async with db_pool.acquire() as conn:
            # Проверяем, занято ли время
            exists = await conn.fetchval("""
                SELECT 1 FROM bookings
                WHERE table_id = $1 AND time_slot = $2 AND DATE(booking_for) = $3
            """, table_id, time_slot, date)

            if exists:
                return jsonify({"status": "error", "message": "Это время уже забронировано"}), 400

            # Вставляем новую бронь
            await conn.execute("""
                INSERT INTO bookings (table_id, time_slot, booking_for, phone, user_id, user_name, guests)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
            """, table_id, time_slot, booking_for, phone, user_id, user_name, guests)

        return jsonify({"status": "ok", "message": "Столик успешно забронирован"})

    except Exception as e:
        print("❌ Ошибка /book:", e)
        return jsonify({"status": "error", "message": str(e)}), 500


# ------------------------------------------------------------------------------
# 🔹 Главная страница (для проверки)
# ------------------------------------------------------------------------------
@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "ok", "message": "Booking backend is running"})


# ------------------------------------------------------------------------------
# Запуск сервера
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(init_db())
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
