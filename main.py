import asyncio
import logging
import sqlite3
import time
from datetime import datetime

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

# ================= КОНФИГУРАЦИЯ =================
BOT_TOKEN = "8774307062:AAG0Al9YXrNw5Awl8ikiD4ySKOaz1S9bKYI"
# ВАЖНО: Замените на реальный ID группы администрации (начинается с -100)
ADMIN_GROUP_ID = -1004330975091
DB_PATH = "school_tickets.db"

# Анти-спам настройки
anti_spam_cache = {}
COOLDOWN_SECONDS = 60

router = Router()

# ================= БАЗА ДАННЫХ =================
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                has_consented BOOLEAN DEFAULT 0
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tickets (
                ticket_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                admin_message_id INTEGER,
                is_anonymous BOOLEAN,
                sender_info TEXT,
                created_at DATETIME
            )
        ''')
        conn.commit()

# ================= СОСТОЯНИЯ (FSM) =================
class AppealState(StatesGroup):
    choosing_type = State()
    entering_name = State()
    writing_appeal = State()

# ================= КЛАВИАТУРЫ =================
def get_consent_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Я согласен(на)", callback_data="consent_accept")]
    ])

def get_appeal_type_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🕵️‍♂️ Анонимно", callback_data="type_anonymous")],
        [InlineKeyboardButton(text="👤 Представиться", callback_data="type_named")]
    ])

# ================= ХЭНДЛЕРЫ РОДИТЕЛЕЙ =================
@router.message(CommandStart(), F.chat.type == "private")
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT has_consented FROM users WHERE user_id = ?", (message.from_user.id,))
        user = cursor.fetchone()

    if not user or not user[0]:
        consent_text = (
            "Добрый день! Вы запустили чат-бот «Острый вопрос» для связи с администрацией.\n\n"
            "⚠️ **Согласие на обработку персональных данных**\n"
            "В соответствии с Законом РБ от 07.05.2021 № 99-З «О защите персональных данных», "
            "для работы с ботом необходимо ваше согласие.\n\n"
            "**Оператор:** ГУО «Средняя школа №191 г. Минска имени И.П. Паромчика».\n"
            "**Цель обработки:** рассмотрение обращений и обеспечение обратной связи.\n\n"
            "*Нажимая кнопку ниже, вы даете свое информированное согласие на обработку ваших данных.*"
        )
        await message.answer(consent_text, reply_markup=get_consent_kb())
    else:
        await ask_appeal_type(message, state)

@router.callback_query(F.data == "consent_accept")
async def process_consent(callback: CallbackQuery, state: FSMContext):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO users (user_id, has_consented) VALUES (?, ?)",
            (callback.from_user.id, True)
        )
        conn.commit()
    
    await callback.message.edit_text("✅ Согласие получено. Спасибо!")
    await ask_appeal_type(callback.message, state)
    await callback.answer()

async def ask_appeal_type(message: Message, state: FSMContext):
    await message.answer("Как вы хотите задать вопрос?", reply_markup=get_appeal_type_kb())
    await state.set_state(AppealState.choosing_type)

@router.callback_query(AppealState.choosing_type, F.data.in_(["type_anonymous", "type_named"]))
async def process_appeal_type(callback: CallbackQuery, state: FSMContext):
    is_anonymous = (callback.data == "type_anonymous")
    await state.update_data(is_anonymous=is_anonymous)
    
    if is_anonymous:
        await callback.message.edit_text(
            "🕵️‍♂️ Выбрано: **Анонимно**.\n\nНапишите ваше обращение одним сообщением (можно прикрепить фото):"
        )
        await state.set_state(AppealState.writing_appeal)
    else:
        await callback.message.edit_text(
            "👤 Выбрано: **Представиться**.\n\nПожалуйста, введите ваше ФИО и класс (например: *Иванов Иван Иванович, 5 \"А\"*):"
        )
        await state.set_state(AppealState.entering_name)
    await callback.answer()

@router.message(AppealState.entering_name, F.chat.type == "private")
async def process_name(message: Message, state: FSMContext):
    await state.update_data(sender_info=message.text)
    await message.answer("Отлично. Теперь напишите суть вашего обращения одним сообщением (можно прикрепить фото):")
    await state.set_state(AppealState.writing_appeal)

@router.message(AppealState.writing_appeal, F.chat.type == "private")
async def process_appeal(message: Message, state: FSMContext, bot: Bot):
    global anti_spam_cache
    user_id = message.from_user.id
    current_time = time.time()

    # Ленивая очистка кэша от старых записей
    anti_spam_cache = {k: v for k, v in anti_spam_cache.items() if current_time - v < COOLDOWN_SECONDS}

    # Проверка анти-спама
    if user_id in anti_spam_cache:
        time_passed = current_time - anti_spam_cache[user_id]
        if time_passed < COOLDOWN_SECONDS:
            remaining_time = int(COOLDOWN_SECONDS - time_passed)
            await message.answer(
                f"⏳ Пожалуйста, не спешите. Вы сможете отправить следующее обращение через {remaining_time} сек.\n"
                "Ваш текст сохранен, просто отправьте его снова после истечения таймера."
            )
            return

    # Фиксируем время отправки
    anti_spam_cache[user_id] = current_time

    data = await state.get_data()
    is_anonymous = data.get("is_anonymous")
    sender_info = data.get("sender_info", "Анонимно")
    appeal_text = message.text or message.caption or "[Медиафайл/Стикер без текста]"

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO tickets (user_id, is_anonymous, sender_info, created_at) VALUES (?, ?, ?, ?)",
            (user_id, is_anonymous, sender_info, datetime.now())
        )
        ticket_id = cursor.lastrowid
        conn.commit()

    admin_text = (
        f"🔴 **Новое обращение #{ticket_id}**\n"
        f"**От кого:** {sender_info}\n\n"
        f"**Текст:**\n{appeal_text}\n\n"
        f"💬 *Чтобы ответить, сделайте Reply (Ответить) на это сообщение.*"
    )

    try:
        admin_msg = await bot.send_message(chat_id=ADMIN_GROUP_ID, text=admin_text)
        
        if message.photo or message.document:
            await message.copy_to(chat_id=ADMIN_GROUP_ID)

        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE tickets SET admin_message_id = ? WHERE ticket_id = ?",
                (admin_msg.message_id, ticket_id)
            )
            conn.commit()

        await message.answer(f"✅ Ваше обращение №{ticket_id} принято и передано администрации. Ожидайте ответа.")
    except Exception as e:
        logging.error(f"Ошибка отправки админам: {e}")
        await message.answer("❌ Произошла ошибка при отправке. Убедитесь, что бот добавлен в группу администрации.")
    
    await state.clear()

# ================= ХЭНДЛЕРЫ АДМИНИСТРАЦИИ =================
@router.message(F.chat.id == ADMIN_GROUP_ID, F.reply_to_message)
async def admin_reply_handler(message: Message, bot: Bot):
    if message.reply_to_message.from_user.id != bot.id:
        return

    admin_msg_id = message.reply_to_message.message_id

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, ticket_id FROM tickets WHERE admin_message_id = ?", (admin_msg_id,))
        ticket = cursor.fetchone()

    if ticket:
        user_id, ticket_id = ticket
        reply_text = (
            f"🔔 **Ответ администрации на ваше обращение №{ticket_id}:**\n\n"
            f"{message.text}"
        )
        try:
            await bot.send_message(chat_id=user_id, text=reply_text)
            await message.reply("✅ Ответ успешно доставлен.")
        except Exception as e:
            await message.reply(f"❌ Ошибка доставки (пользователь мог заблокировать бота).\nЛог: {e}")
    else:
        await message.reply("⚠️ Не удалось найти автора обращения в базе данных.")

# ================= ЗАПУСК =================
async def main():
    init_db()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
    dp = Dispatcher()
    dp.include_router(router)
    
    print("Бот успешно запущен и готов к работе!")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
