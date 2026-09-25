"""
Телеграм-бот для записи клиентов к специалистам (тренеры, мастера бьюти-сферы,
репетиторы и т.д. — специалист сам пишет, чем занимается, при регистрации).

Вся работа — и кабинет специалиста (услуги, расписание, клиенты), и запись
клиента — происходит внутри Telegram Mini App. Этот файл теперь отвечает
только за: /start (привязка клиента по ссылке специалиста + одна кнопка входа
в приложение), уведомления (новая запись/отмена шлёт webapp.py напрямую через
bot.send_message, напоминания и бэкап шлёт этот файл по расписанию) и
служебные команды.
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import (
    Message,
    ErrorEvent,
    FSInputFile,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from aiohttp import web as aioweb

import db
import webapp

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

DAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
MONTHS_RU = ["", "января", "февраля", "марта", "апреля", "мая", "июня",
             "июля", "августа", "сентября", "октября", "ноября", "декабря"]
SUPPORT_CONTACT = "@gmqzn"  # по вопросам к боту пишут сюда
ADMIN_ID = 660762742  # твой telegram id — только тебе доступен /reset_all
MSK = ZoneInfo("Europe/Moscow")
MINI_APP_URL = os.getenv("MINI_APP_URL", "").rstrip("/")  # базовый https-домен для мини-приложения
PORT = int(os.getenv("PORT", "8080"))


def now_msk() -> datetime:
    """Текущее время по Москве — сервер бота крутится не в московском часовом поясе,
    поэтому весь расчёт 'сегодня/сейчас' идёт через эту функцию, а не datetime.now()."""
    return datetime.now(MSK)


def esc(text: str) -> str:
    return escape(text or "")


def fmt_slot(slot_dt: str) -> str:
    dt = datetime.strptime(slot_dt, "%Y-%m-%d %H:%M")
    return f"{DAYS_RU[dt.weekday()]}, {dt.day} {MONTHS_RU[dt.month]} в {dt.strftime('%H:%M')}"


def open_app_kb() -> InlineKeyboardMarkup | None:
    """Единственная кнопка входа в мини-приложение. Роль (специалист/клиент/гость) определяет
    само приложение через /api/whoami по initData — никаких query-параметров в URL не нужно.

    К ссылке добавлен метка времени: одна и та же ссылка, открытая второй раз подряд, иногда
    не долетала до Telegram как 'новый' запуск (initData оставался пустым — вероятно, WebKit
    отдавал закэшированную страницу вместо честной новой загрузки). Уникальный параметр на
    каждый показ кнопки решает это."""
    if not MINI_APP_URL:
        return None
    url = f"{MINI_APP_URL}/miniapp/index.html?_t={int(now_msk().timestamp())}"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🚀 Открыть приложение", web_app=WebAppInfo(url=url))]]
    )


# ---------- /start, /help ----------

@dp.message(Command("start"))
async def cmd_start(message: Message, command: CommandObject):
    payload = (command.args or "").strip()
    if payload.isdigit() and db.get_trainer(int(payload)):
        # Переход по персональной ссылке специалиста — привязываем клиента.
        db.link_client(message.from_user.id, int(payload), message.from_user.full_name, message.from_user.username)

    kb = open_app_kb()
    if not kb:
        await message.answer("⚠️ Приложение временно недоступно, попробуй чуть позже.")
        return

    trainer = db.get_trainer(message.from_user.id)
    if trainer:
        text = f"С возвращением, <b>{esc(trainer['name'])}</b>! 👋\nОткрывай приложение — там всё управление."
    else:
        text = "👋 <b>Привет!</b>\nЖми на кнопку, чтобы открыть приложение."
    await message.answer(text, reply_markup=kb)


@dp.message(Command("help"))
async def cmd_help(message: Message):
    kb = open_app_kb()
    await message.answer(
        "Всё управление и запись — внутри приложения: жми кнопку ниже.\n\n"
        f"🛟 Вопросы по боту — пиши {SUPPORT_CONTACT}",
        reply_markup=kb,
    )


@dp.message(Command("reset_all"))
async def cmd_reset_all(message: Message):
    """Только для тебя — полный сброс данных при тестировании: /reset_all confirm"""
    if message.from_user.id != ADMIN_ID:
        return
    if (message.text or "").strip() != "/reset_all confirm":
        await message.answer(
            "⚠️ Это удалит ВСЕХ специалистов, клиентов и записи без возможности отмены.\n"
            "Чтобы подтвердить, напиши: <code>/reset_all confirm</code>"
        )
        return
    db.reset_all()
    await message.answer("🗑 Готово, база очищена.")


# ---------- Напоминания и бэкап ----------

async def send_reminders():
    now = now_msk()

    win24_start = (now + timedelta(hours=23)).strftime("%Y-%m-%d %H:%M")
    win24_end = (now + timedelta(hours=25)).strftime("%Y-%m-%d %H:%M")
    for slot in db.slots_needing_reminder("reminder_24h_sent", win24_start, win24_end):
        try:
            await bot.send_message(
                slot["client_id"],
                f"⏰ Напоминаю: завтра в <b>{slot['slot_dt'][-5:]}</b> у тебя запись"
                f"{' (' + esc(slot['service_name']) + ')' if slot['service_name'] else ''}.",
            )
        except Exception:
            logger.warning("Не удалось отправить напоминание клиенту %s", slot["client_id"])
        db.mark_reminder_sent(slot["id"], "reminder_24h_sent")

    win1_start = (now + timedelta(minutes=50)).strftime("%Y-%m-%d %H:%M")
    win1_end = (now + timedelta(minutes=70)).strftime("%Y-%m-%d %H:%M")
    for slot in db.slots_needing_reminder("reminder_1h_sent", win1_start, win1_end):
        try:
            await bot.send_message(
                slot["client_id"],
                f"⏰ Через час у тебя запись (<b>{slot['slot_dt'][-5:]}</b>). Не забудь!",
            )
        except Exception:
            logger.warning("Не удалось отправить напоминание клиенту %s", slot["client_id"])
        db.mark_reminder_sent(slot["id"], "reminder_1h_sent")


async def send_db_backup():
    """Раз в сутки шлёт файл базы тебе в личку — на случай, если volume на Railway когда-нибудь потеряется."""
    try:
        await bot.send_document(
            ADMIN_ID,
            FSInputFile(db.DB_PATH),
            caption=f"🗄 Бэкап базы на {now_msk().strftime('%Y-%m-%d %H:%M')} (МСК)",
        )
    except Exception:
        logger.warning("Не удалось отправить бэкап базы")


async def setup_bot_profile():
    """Описание бота в профиле Telegram — со ссылкой на поддержку."""
    try:
        await bot.set_my_description(
            description=(
                "Запись на приём/тренировку/услугу — по персональной ссылке специалиста.\n\n"
                f"По вопросам пиши {SUPPORT_CONTACT} 🛟"
            )
        )
        await bot.set_my_short_description(
            short_description=f"Онлайн-запись к своему специалисту. Вопросы — {SUPPORT_CONTACT}"
        )
    except Exception:
        logger.warning("Не удалось обновить описание бота")


# ---------- Обработка ошибок ----------

@dp.error()
async def error_handler(event: ErrorEvent):
    """Ловит все необработанные исключения, чтобы бот не молчал и не падал молча:
    логирует, шлёт тебе алерт в личку (с указанием chat_id исходного обновления) и
    отвечает юзеру, что что-то пошло не так.

    Исключение: TelegramForbiddenError (кто-то заблокировал бота) — это не баг,
    а ожидаемое поведение, просто тихо логируем без алерта."""
    update = event.update
    chat_id = None
    if update.message:
        chat_id = update.message.chat.id
    elif update.callback_query and update.callback_query.message:
        chat_id = update.callback_query.message.chat.id

    if isinstance(event.exception, TelegramForbiddenError):
        logger.warning("Заблокировали бота (chat_id=%s) — пропускаю алерт", chat_id)
        return True

    logger.exception("Необработанное исключение", exc_info=event.exception)

    try:
        await bot.send_message(
            ADMIN_ID,
            f"⚠️ <b>Ошибка в боте</b> (chat_id={chat_id})\n<code>{esc(str(event.exception))}</code>",
        )
    except Exception:
        logger.warning("Не удалось отправить алерт об ошибке админу")

    try:
        if chat_id:
            await bot.send_message(chat_id, "⚠️ Что-то пошло не так. Попробуй ещё раз чуть позже.")
    except Exception:
        logger.warning("Не удалось уведомить пользователя об ошибке")

    return True


async def main():
    if not BOT_TOKEN:
        raise SystemExit("Заполни BOT_TOKEN в файле .env")

    db.init_db()
    await setup_bot_profile()
    me = await bot.get_me()
    bot_username = me.username

    scheduler = AsyncIOScheduler()
    scheduler.add_job(send_reminders, "interval", minutes=5)
    scheduler.add_job(send_db_backup, "cron", hour=6, minute=0, timezone=MSK)
    scheduler.start()

    # Веб-сервер мини-приложения (API + статика) — крутится в этом же процессе,
    # рядом с long polling бота, на порту, который выдаёт Railway.
    app = webapp.create_app(bot, BOT_TOKEN, bot_username)
    runner = aioweb.AppRunner(app)
    await runner.setup()
    site = aioweb.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info("Веб-сервер мини-приложения запущен на порту %s", PORT)

    logger.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
