import os
import io
import logging
import asyncio
import re
import qrcode
from flask import Flask, render_template, send_file
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv
from threading import Thread

load_dotenv()

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Переменные окружения
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
PORT = int(os.getenv('PORT', 8443))

# Инициализация Flask приложения
flask_app = Flask(__name__)

# Хранилище QR кодов (в памяти)
qr_storage = {}

@flask_app.route('/qr/<qr_id>')
def show_qr(qr_id):
    """Отображение QR кода в браузере"""
    if qr_id not in qr_storage:
        return 'QR код не найден', 404
    
    text_data = qr_storage[qr_id]
    return render_template('qr.html', qr_id=qr_id, text=text_data)

@flask_app.route('/qr/<qr_id>/image')
def get_qr_image(qr_id):
    """Получить QR код как изображение (PNG)"""
    if qr_id not in qr_storage:
        return 'QR код не найден', 404
    
    text_data = qr_storage[qr_id]
    colors = qr_storage.get(f"{qr_id}_colors", {"fill": "black", "back": "white"})
    
    # Генерируем QR код
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=2,
    )
    qr.add_data(text_data)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color=colors["fill"], back_color=colors["back"])
    
    # Сохраняем в памяти и отправляем
    img_io = io.BytesIO()
    img.save(img_io, 'PNG')
    img_io.seek(0)
    
    return send_file(img_io, mimetype='image/png')

@flask_app.route('/health')
def health():
    """Проверка здоровья приложения"""
    return {'status': 'ok'}, 200

def is_valid_hex(color):
    """
    Валидирует HEX цвет по regex ^#([A-Fa-f0-9]{6})$
    """
    return bool(re.match(r'^#([A-Fa-f0-9]{6})$', color))

def parse_text_and_colors(input_text):
    """
    Парсит входящее сообщение и извлекает:
    - Основной текст/ссылку
    - Цвет QR-кода (fill_color)
    - Цвет фона (back_color)
    
    Правила:
    - Нет RGB кодов → fill_color = #000000 (черный), back_color = #ffffff (белый) — дефолт
    - Только 1 RGB код → fill_color = parts[1], back_color = #ffffff (дефолт)
    - 2 RGB кода → fill_color = parts[1], back_color = parts[2]
    
    Возвращает словарь: {
        'text': строка,
        'fill_color': цвет QR,
        'back_color': цвет фона
    }
    """
    parts = input_text.strip().split()
    
    # parts[0] всегда основной текст/ссылка
    main_text = parts[0]
    
    # Сброс к дефолтным цветам
    fill_color = "#000000"  # черный QR
    back_color = "#ffffff"  # белый фон
    
    # Проверяем части после основного текста
    if len(parts) > 1 and is_valid_hex(parts[1]):
        # Есть первый HEX код - это цвет QR
        fill_color = parts[1]
        
        # Если есть второй HEX код - это цвет фона
        if len(parts) > 2 and is_valid_hex(parts[2]):
            back_color = parts[2]
    
    return {
        'text': main_text,
        'fill_color': fill_color,
        'back_color': back_color
    }

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start"""
    welcome_message = """
🤖 Добро пожаловать в QR Code Bot!

Просто отправьте мне любой текст, и я преобразую его в QR код:
✅ Фото QR кода в чате
✅ Ссылка для просмотра в браузере
✅ Возможность печати

🎨 <b>Кастомные цвета:</b>
Отправьте сообщение в формате: <code>текст #RGB_QR #RGB_фон</code>

Примеры:
• <code>https://google.com</code> → черный QR, белый фон (дефолт)
• <code>https://google.com #8C1AFF</code> → фиолетовый QR, белый фон
• <code>https://google.com #8C1AFF #ffffff</code> → фиолетовый QR, белый фон

Попробуйте уже сейчас! Отправьте любой текст 📝
    """
    await update.message.reply_text(welcome_message, parse_mode='HTML')

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик текстовых сообщений"""
    text = update.message.text.strip()
    
    # Проверка на пустое сообщение
    if not text or len(text) > 2953:  # Максимальная длина для QR кода
        await update.message.reply_text("❌ Пожалуйста, отправьте текст (максимум 2953 символа)")
        return
    
    # Парсим текст и извлекаем цвета
    parsed_data = parse_text_and_colors(text)
    main_text = parsed_data['text']
    fill_color = parsed_data['fill_color']
    back_color = parsed_data['back_color']
    
    # Отправляем уведомление о обработке
    await update.message.chat.send_action("upload_photo")
    
    try:
        # Получаем URL приложения
        if os.getenv('RAILWAY_PUBLIC_DOMAIN'):
            bot_url = f"https://{os.getenv('RAILWAY_PUBLIC_DOMAIN')}"
        else:
            bot_url = (os.getenv('WEBHOOK_URL') or 'http://localhost:8443').rstrip('/')
        
        # Генерируем уникальный ID для QR кода
        qr_id = f"{update.message.chat_id}_{update.message.message_id}"
        
        # Сохраняем текст
        qr_storage[qr_id] = main_text
        
        # Сохраняем цвета
        qr_storage[f"{qr_id}_colors"] = {
            "fill": fill_color,
            "back": back_color
        }
        
        # Генерируем QR код
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=2,
        )
        qr.add_data(main_text)
        qr.make(fit=True)
        
        img = qr.make_image(fill_color=fill_color, back_color=back_color)
        
        # Сохраняем в памяти
        img_io = io.BytesIO()
        img.save(img_io, 'PNG')
        img_io.seek(0)
        
        # Создаем кнопку со ссылкой
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🌐 Открыть в браузере", url=f"{bot_url}/qr/{qr_id}")]
        ])
        
        # Формируем текст подписи
        caption_text = main_text
        if fill_color != "#000000" or back_color != "#ffffff":
            caption_text += f"\n\n🎨 Цвета:\n• QR: {fill_color}\n• Фон: {back_color}"
        
        # Отправляем фото QR кода c исходный текстом
        await update.message.reply_photo(
            photo=img_io,
            caption=caption_text,
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"Ошибка при обработке текста: {e}", exc_info=True)
        await update.message.reply_text("❌ Ошибка при создании QR кода. Попробуйте снова.")

async def handle_other(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Игнорируем все остальное (гифы, стикеры, фото и т.д.)"""
    pass

def run_flask():
    """Запуск Flask приложения"""
    flask_app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

async def run_bot():
    """Запуск Telegram бота в polling режиме"""
    # Проверка необходимых переменных окружения
    if not TELEGRAM_TOKEN:
        logger.error("❌ TELEGRAM_TOKEN не установлен!")
        return
    
    logger.info("🤖 Запуск Telegram бота в polling режиме...")
    
    # Создаем приложение бота
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Добавляем обработчики
    app.add_handler(CommandHandler("start", start))
    
    # Обрабатываем только текстовые сообщения
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    # Игнорируем все остальное (гифы, стикеры, фото и т.д.)
    app.add_handler(MessageHandler(~filters.TEXT, handle_other))
    
    logger.info("✅ Обработчики добавлены")
    
    # Запускаем бот в polling режиме
    async with app:
        await app.start()
        logger.info("✅ Telegram бот запущен в polling режиме!")
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        
        # Бот работает до прерывания
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logger.info("Бот остановлен пользователем")
        finally:
            await app.updater.stop()
            await app.stop()

async def main():
    """Основная функция"""
    # Запускаем Flask в отдельном потоке
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("✅ Flask сервер запущен на 0.0.0.0:8443")
    
    # Даем Flask время на запуск
    await asyncio.sleep(1)
    
    # Запускаем бот в основном потоке
    await run_bot()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Приложение остановлено")
