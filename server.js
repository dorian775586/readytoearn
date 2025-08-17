// server.js
import express from 'express';
import cors from 'cors';
import { v4 as uuidv4 } from 'uuid';

const app = express();

// ------------------------------------------------------------------
// 1️⃣ Порт
// Render задаёт его в переменной PORT, а если вы запускаете локально – берём 3000.
const PORT = process.env.PORT || 3000;

// ------------------------------------------------------------------
// 2️⃣ Middleware
app.use(cors({ origin: '*' }));   // разрешаем запросы от любых доменов (Vercel, Telegram и т.п.)
app.use(express.json());          // парсим JSON‑тело запросов

// ------------------------------------------------------------------
// 3️⃣ Обработчик бронирования
app.post('/book', (req, res) => {
  const { table, date, time, guests, phone } = req.body;

  // простая проверка – в приложении всегда будет table, time, guests
  if (!table || !time || !guests) {
    return res.status(400).json({ error: 'Missing fields' });
  }

  const booking = {
    id: uuidv4(),
    table,
    date,
    time,
    guests,
    phone,
    createdAt: new Date()
  };

  console.log('🆕 New booking:', booking);   // будет виден в логах Render
  res.json(booking);                        // отсылаем клиенту подтверждение
});

// ------------------------------------------------------------------
// 4️⃣ Запуск (Render) / экспорт (если вдруг захотите использовать на Vercel)
app.listen(PORT, () => {
  console.log(`🚀 Server listening on http://0.0.0.0:${PORT}`);
});
