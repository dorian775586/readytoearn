import os
import psycopg2
import sys # Добавляем для более чистого завершения при ошибке

# ====================================================================
# Инициализация базы данных PostgreSQL на Render
# (Требует, чтобы переменная окружения DATABASE_URL была задана)
# ====================================================================

# Получаем URL базы из переменных окружения
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    print("Ошибка: Переменная DATABASE_URL не задана!")
    sys.exit(1)

try:
    # 🛠 КЛЮЧЕВОЕ ИЗМЕНЕНИЕ: Добавляем sslmode='require' для Render.
    conn = psycopg2.connect(
        DATABASE_URL,
        sslmode='require' 
    )
    cursor = conn.cursor()

    # Создаём таблицу столиков
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tables (
        id SERIAL PRIMARY KEY
    )
    """)

    # Создаём таблицу бронирований
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS bookings (
        booking_id SERIAL PRIMARY KEY,
        table_id INTEGER NOT NULL,
        time_slot TEXT NOT NULL,
        booked_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP, -- Изменено для соответствия lis.py
        booking_for TIMESTAMP, -- Изменено для соответствия lis.py
        phone TEXT NOT NULL,
        user_id BIGINT,       -- Добавлено для соответствия lis.py
        user_name VARCHAR(255), -- Добавлено для соответствия lis.py
        guests INT              -- Добавлено для соответствия lis.py
    )
    """)

    # Заполняем 10 столиков (id от 1 до 10), избегая дубликатов
    cursor.execute("SELECT COUNT(*) FROM tables;")
    if cursor.fetchone()[0] == 0:
        cursor.executemany("INSERT INTO tables (id) VALUES (%s)", [(i,) for i in range(1, 11)])

    # Сохраняем изменения и закрываем соединение
    conn.commit()
    conn.close()

    print("База и таблицы успешно созданы и заполнены.")

except psycopg2.OperationalError as e:
    # Отдельная обработка ошибок подключения (включая SSL)
    print(f"❌ Ошибка подключения к базе данных! Проверьте DATABASE_URL и SSL-настройки.")
    print(f"Детали ошибки: {e}")
    sys.exit(1)

except Exception as e:
    print(f"❌ Произошла непредвиденная ошибка в baza.py: {e}")
    sys.exit(1)