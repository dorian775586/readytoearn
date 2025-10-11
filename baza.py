import os
import psycopg2
import sys 

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
        booked_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP, 
        booking_for TIMESTAMP, 
        phone TEXT NOT NULL,
        user_id BIGINT,      
        user_name VARCHAR(255), 
        guests INT           
    )
    """)
    
    # ========================================================
    # ДОБАВЛЕНИЕ ИНДЕКСОВ ДЛЯ ОПТИМИЗАЦИИ
    # ========================================================
    # 1. Индекс для быстрой проверки конфликтов (table_id, booking_for)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bookings_conflict ON bookings (table_id, booking_for);")
    
    # 2. Индекс для быстрого поиска активной брони пользователя (Моя бронь)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bookings_user_active ON bookings (user_id, booking_for DESC);")
    
    # 3. Индекс для админ-панели (будущие брони) и общей сортировки по дате брони
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bookings_future_time ON bookings (booking_for);")
    
    # 4. Индекс для истории (сортировка по дате создания брони)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bookings_booked_at ON bookings (booked_at DESC);")
    # ========================================================


    # Заполняем 20 столиков (id от 1 до 20), избегая дубликатов
    cursor.execute("SELECT COUNT(*) FROM tables;")
    if cursor.fetchone()[0] == 0:
        # ИСПРАВЛЕНО: Теперь создаются 20 столов, чтобы соответствовать фронтенду
        cursor.executemany("INSERT INTO tables (id) VALUES (%s)", [(i,) for i in range(1, 21)]) 

    # Сохраняем изменения и закрываем соединение
    conn.commit()
    conn.close()

    print("База, таблицы и индексы успешно созданы и заполнены.")

except psycopg2.OperationalError as e:
    # Отдельная обработка ошибок подключения (включая SSL)
    print(f"❌ Ошибка подключения к базе данных! Проверьте DATABASE_URL и SSL-настройки.")
    print(f"Детали ошибки: {e}")
    sys.exit(1)

except Exception as e:
    print(f"❌ Произошла непредвиденная ошибка в baza.py: {e}")
    sys.exit(1)