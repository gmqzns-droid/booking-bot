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
    CallbackQuery,
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

# user_id -> review_id: ждём от этого клиента комментарий следующим сообщением после того,
# как он поставил оценку (тонкая, но осознанно простая замена полноценному FSM — сценарий
# однострочный и разрывать его состоянием ради одного поля избыточно).
PENDING_REVIEW_COMMENT: dict[int, int] = {}


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


# ---------- Отзывы после визита ----------

@dp.callback_query(F.data.startswith("rv:"))
async def cb_review_rating(callback: CallbackQuery):
    try:
        _, slot_id_str, rating_str = callback.data.split(":")
        slot_id, rating = int(slot_id_str), int(rating_str)
    except (ValueError, AttributeError):
        await callback.answer()
        return

    slot = db.get_slot(slot_id)
    if not slot or slot["client_id"] != callback.from_user.id:
        await callback.answer("Эта запись не найдена", show_alert=True)
        return

    review_id = db.add_review(
        slot_id, slot["trainer_id"],
        slot["staff_id"] if "staff_id" in slot.keys() else None,
        slot["staff_name"] if "staff_name" in slot.keys() else None,
        callback.from_user.id, callback.from_user.full_name, rating,
    )
    if review_id is None:
        await callback.answer("Ты уже оценил(а) эту запись, спасибо!", show_alert=True)
        return

    PENDING_REVIEW_COMMENT[callback.from_user.id] = review_id
    stars = "⭐" * rating
    try:
        await callback.message.edit_text(
            f"Спасибо за оценку! {stars}\n\n"
            f"Если хочешь — напиши пару слов отзыва следующим сообщением. Необязательно."
        )
    except Exception:
        pass
    await callback.answer()


@dp.message(F.text & ~F.text.startswith("/"))
async def handle_plain_text(message: Message):
    """Единственное, чего мы ждём вне команд и мини-приложения — комментарий к отзыву
    сразу после того, как клиент поставил оценку. Всё остальное тихо игнорируем."""
    review_id = PENDING_REVIEW_COMMENT.pop(message.from_user.id, None)
    if review_id is None:
        return
    comment = (message.text or "").strip()[:500]
    if comment:
        db.set_review_comment(review_id, comment)
        await message.answer("Спасибо, добавил(а) твой отзыв! 🙏")


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


async def send_review_requests():
    """Раз в 20 минут спрашивает оценку у тех, чей визит прошёл 2+ часа назад (и не старше
    26 часов — чтобы не заваливать клиентов старыми просьбами после простоя/редеплоя)."""
    for slot in db.slots_needing_review():
        trainer = db.get_trainer(slot["trainer_id"])
        if not trainer:
            db.mark_review_requested(slot["id"])
            continue
        staff_name = (
            slot["staff_name"] if "staff_name" in slot.keys() and slot["staff_name"] else trainer["name"]
        )
        who_line = f" у <b>{esc(staff_name)}</b>" if trainer["is_business"] else ""
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=f"{i}⭐", callback_data=f"rv:{slot['id']}:{i}") for i in range(1, 6)
        ]])
        try:
            await bot.send_message(
                slot["client_id"],
                f"Как прошёл визит{who_line}? Оцени от 1 до 5 — это очень помогает специалисту.",
                reply_markup=kb,
            )
        except Exception:
            logger.warning("Не удалось отправить запрос на отзыв клиенту %s", slot["client_id"])
        db.mark_review_requested(slot["id"])


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
    scheduler.add_job(send_review_requests, "interval", minutes=20)
    scheduler.add_job(send_db_backup, "cron", hour=6, minute=0, timezone=MSK)
    scheduler.start()

    # Веб-сервер мини-приложения (API + статика) — крутится в этом же процессе,
    # рядом с long polling бота, на порту, который выдаёт Railway.
    app = webapp.create_app(bot, BOT_TOKEN, bot_username, MINI_APP_URL)
    runner = aioweb.AppRunner(app)
    await runner.setup()
    site = aioweb.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info("Веб-сервер мини-приложения запущен на порту %s", PORT)

    logger.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
