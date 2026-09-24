"""
Телеграм-бот для записи клиентов к тренерам. v2 — полностью на кнопках.

Любой человек может зарегистрироваться как тренер прямо в боте (/start -> "Я тренер").
Тренер добавляет свободное время через кнопки (день -> время).
Клиенты выбирают тренера (если их несколько), день и время и бронируют в один клик.
Бот сам шлёт клиенту напоминания за 24 часа и за 1 час до тренировки.
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta
from html import escape

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

import db

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

DAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
MONTHS_RU = ["", "января", "февраля", "марта", "апреля", "мая", "июня",
             "июля", "августа", "сентября", "октября", "ноября", "декабря"]
QUICK_TIMES = ["09:00", "10:00", "11:00", "12:00", "14:00", "15:00",
               "16:00", "17:00", "18:00", "19:00", "20:00"]


# ---------- FSM состояния ----------

class TrainerOnboarding(StatesGroup):
    waiting_name = State()
    waiting_specialty = State()


class AddSlot(StatesGroup):
    waiting_custom_time = State()


# ---------- Утилиты ----------

def esc(text: str) -> str:
    return escape(text or "")


def fmt_day(day: str) -> str:
    dt = datetime.strptime(day, "%Y-%m-%d")
    return f"{DAYS_RU[dt.weekday()]}, {dt.day} {MONTHS_RU[dt.month]}"


def fmt_slot(slot_dt: str) -> str:
    dt = datetime.strptime(slot_dt, "%Y-%m-%d %H:%M")
    return f"{DAYS_RU[dt.weekday()]}, {dt.day} {MONTHS_RU[dt.month]} в {dt.strftime('%H:%M')}"


def trainer_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Добавить время")],
            [KeyboardButton(text="📋 Мои записи"), KeyboardButton(text="🙋 Я как клиент")],
        ],
        resize_keyboard=True,
    )


def client_menu(is_trainer_too: bool = False) -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton(text="🔍 Записаться")],
        [KeyboardButton(text="🗓 Мои записи")],
    ]
    if is_trainer_too:
        keyboard.append([KeyboardButton(text="🧑‍🏫 Кабинет тренера")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def next_14_days() -> list[str]:
    return [(datetime.now() + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(14)]


# ---------- /start и регистрация тренера ----------

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    trainer = db.get_trainer(message.from_user.id)
    if trainer:
        await message.answer(
            f"С возвращением, <b>{esc(trainer['name'])}</b>! 👋\n"
            f"Это твой кабинет тренера — отсюда управляешь расписанием.",
            reply_markup=trainer_menu(),
        )
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🧑‍🏫 Я тренер", callback_data="role:trainer")],
            [InlineKeyboardButton(text="🙋 Хочу записаться на тренировку", callback_data="role:client")],
        ]
    )
    await message.answer(
        "👋 <b>Привет! Это бот для записи на тренировки.</b>\n\n"
        "🧑‍🏫 Тренеры ведут здесь расписание и получают записи автоматически.\n"
        "🙋 Клиенты в пару кликов выбирают время и записываются.\n\n"
        "Кто ты?",
        reply_markup=kb,
    )


@dp.callback_query(F.data == "role:client")
async def cb_role_client(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("🙋 Отлично, вот твоё меню!")
    await callback.message.answer(
        "Выбирай, что нужно 👇",
        reply_markup=client_menu(is_trainer_too=db.is_trainer(callback.from_user.id)),
    )
    await callback.answer()


@dp.callback_query(F.data == "role:trainer")
async def cb_role_trainer(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("🧑‍🏫 Как тебя подписывать клиентам? Напиши имя.")
    await state.set_state(TrainerOnboarding.waiting_name)
    await callback.answer()


@dp.message(StateFilter(TrainerOnboarding.waiting_name))
async def onb_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await state.set_state(TrainerOnboarding.waiting_specialty)
    await message.answer("👍 А чем занимаешься? Например: плавание, теннис, репетиторство…")


@dp.message(StateFilter(TrainerOnboarding.waiting_specialty))
async def onb_specialty(message: Message, state: FSMContext):
    data = await state.get_data()
    db.register_trainer(message.from_user.id, data["name"], message.text.strip())
    await state.clear()
    await message.answer(
        f"🎉 Готово, <b>{esc(data['name'])}</b>! Профиль тренера создан.\n"
        f"Теперь добавь свободное время — нажми «➕ Добавить время».",
        reply_markup=trainer_menu(),
    )


# ---------- Тренер: переключение в клиентский режим ----------

@dp.message(F.text == "🙋 Я как клиент")
async def to_client_mode(message: Message):
    await message.answer(
        "🙋 Клиентское меню:",
        reply_markup=client_menu(is_trainer_too=True),
    )


@dp.message(F.text == "🧑‍🏫 Кабинет тренера")
async def to_trainer_mode(message: Message):
    trainer = db.get_trainer(message.from_user.id)
    if not trainer:
        return
    await message.answer(
        f"🧑‍🏫 С возвращением, <b>{esc(trainer['name'])}</b>!",
        reply_markup=trainer_menu(),
    )


# ---------- Тренер: добавление слотов ----------

@dp.message(F.text == "➕ Добавить время")
async def add_slot_pick_day(message: Message):
    if not db.is_trainer(message.from_user.id):
        return
    days = next_14_days()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"📅 {fmt_day(d)}", callback_data=f"addday:{d}")]
            for d in days
        ]
    )
    await message.answer("На какой день добавить свободное время?", reply_markup=kb)


@dp.callback_query(F.data.startswith("addday:"))
async def add_slot_pick_time(callback: CallbackQuery):
    day = callback.data.split(":", 1)[1]
    buttons = [
        InlineKeyboardButton(text=t, callback_data=f"addtime:{day}:{t}") for t in QUICK_TIMES
    ]
    rows = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton(text="✏️ Своё время", callback_data=f"addcustom:{day}")])
    await callback.message.edit_text(
        f"🕐 <b>{fmt_day(day)}</b> — выбери время:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("addtime:"))
async def add_slot_confirm(callback: CallbackQuery):
    _, day, time_str = callback.data.split(":", 2)
    slot_dt = f"{day} {time_str}"
    if db.add_slot(callback.from_user.id, slot_dt):
        await callback.message.edit_text(f"✅ Добавлено: <b>{fmt_slot(slot_dt)}</b>")
    else:
        await callback.message.edit_text("⚠️ Такое время уже было добавлено ранее.")
    await callback.answer()


@dp.callback_query(F.data.startswith("addcustom:"))
async def add_slot_custom_start(callback: CallbackQuery, state: FSMContext):
    day = callback.data.split(":", 1)[1]
    await state.update_data(custom_day=day)
    await state.set_state(AddSlot.waiting_custom_time)
    await callback.message.edit_text(
        f"✏️ <b>{fmt_day(day)}</b> — напиши время в формате ЧЧ:ММ, например <b>13:30</b>"
    )
    await callback.answer()


@dp.message(StateFilter(AddSlot.waiting_custom_time))
async def add_slot_custom_finish(message: Message, state: FSMContext):
    data = await state.get_data()
    day = data["custom_day"]
    text = message.text.strip()
    try:
        hh, mm = map(int, text.split(":"))
        assert 0 <= hh < 24 and 0 <= mm < 60
    except Exception:
        await message.answer("🤔 Не понял время. Формат ЧЧ:ММ, например <b>13:30</b>")
        return
    slot_dt = f"{day} {hh:02d}:{mm:02d}"
    await state.clear()
    if db.add_slot(message.from_user.id, slot_dt):
        await message.answer(f"✅ Добавлено: <b>{fmt_slot(slot_dt)}</b>", reply_markup=trainer_menu())
    else:
        await message.answer("⚠️ Такое время уже было добавлено ранее.", reply_markup=trainer_menu())


# ---------- Тренер: мои записи ----------

@dp.message(F.text == "📋 Мои записи")
async def trainer_my_slots(message: Message):
    if not db.is_trainer(message.from_user.id):
        return
    rows = db.list_all_upcoming(message.from_user.id)
    if not rows:
        await message.answer(
            "Пока нет ни одного слота 🗓\nДобавь через «➕ Добавить время»."
        )
        return
    kb_rows = []
    for r in rows:
        if r["status"] == "booked":
            label = f"🔴 {fmt_slot(r['slot_dt'])} — {r['client_name']}"
        else:
            label = f"🟢 {fmt_slot(r['slot_dt'])} — свободно"
        kb_rows.append([InlineKeyboardButton(text=f"❌ {label}", callback_data=f"trainercancel:{r['id']}")])
    await message.answer(
        "📋 <b>Твоё расписание</b>\n🟢 свободно · 🔴 занято\nНажми на слот, чтобы отменить:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
    )


@dp.callback_query(F.data.startswith("trainercancel:"))
async def trainer_cancel_slot(callback: CallbackQuery):
    slot_id = int(callback.data.split(":")[1])
    slot = db.get_slot(slot_id)
    if not slot or slot["trainer_id"] != callback.from_user.id:
        await callback.answer("Не нашёл этот слот.", show_alert=True)
        return
    was_booked = slot["status"] == "booked"
    db.cancel_slot(slot_id)
    await callback.message.edit_text(f"🗑 Слот <b>{fmt_slot(slot['slot_dt'])}</b> отменён.")
    await callback.answer()
    if was_booked and slot["client_id"]:
        try:
            await bot.send_message(
                slot["client_id"],
                f"⚠️ Тренер отменил запись на <b>{fmt_slot(slot['slot_dt'])}</b>. Извини за неудобство!",
            )
        except Exception:
            logger.warning("Не удалось уведомить клиента %s", slot["client_id"])


# ---------- Клиент: запись ----------

@dp.message(F.text == "🔍 Записаться")
async def client_pick_trainer(message: Message):
    trainers = db.list_trainers()
    if not trainers:
        await message.answer("Пока нет ни одного тренера в системе 😔")
        return
    if len(trainers) == 1:
        await show_days(message.chat.id, trainers[0]["id"])
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=f"🧑‍🏫 {t['name']} ({t['specialty']})" if t["specialty"] else f"🧑‍🏫 {t['name']}",
                callback_data=f"picktrainer:{t['id']}",
            )]
            for t in trainers
        ]
    )
    await message.answer("Выбери тренера 👇", reply_markup=kb)


@dp.callback_query(F.data.startswith("picktrainer:"))
async def client_trainer_picked(callback: CallbackQuery):
    trainer_id = int(callback.data.split(":")[1])
    await callback.message.delete()
    await show_days(callback.message.chat.id, trainer_id)
    await callback.answer()


async def show_days(chat_id: int, trainer_id: int):
    days = db.list_free_days(trainer_id)
    if not days:
        await bot.send_message(chat_id, "У этого тренера пока нет свободного времени 😔")
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"📅 {fmt_day(d)}", callback_data=f"pickday:{trainer_id}:{d}")]
            for d in days
        ]
    )
    await bot.send_message(chat_id, "🗓 Свободные дни:", reply_markup=kb)


@dp.callback_query(F.data.startswith("pickday:"))
async def client_day_picked(callback: CallbackQuery):
    _, trainer_id, day = callback.data.split(":")
    trainer_id = int(trainer_id)
    rows = db.list_free_slots_for_day(trainer_id, day)
    if not rows:
        await callback.answer("На этот день уже нет свободного времени.", show_alert=True)
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"🕐 {r['slot_dt'][-5:]}", callback_data=f"booknow:{r['id']}")]
            for r in rows
        ]
    )
    await callback.message.edit_text(
        f"📅 <b>{fmt_day(day)}</b> — свободное время:", reply_markup=kb
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("booknow:"))
async def client_book_confirm(callback: CallbackQuery):
    slot_id = int(callback.data.split(":")[1])
    client_name = callback.from_user.full_name
    ok = db.book_slot(slot_id, callback.from_user.id, client_name)
    if not ok:
        await callback.answer("Увы, этот слот уже заняли.", show_alert=True)
        return
    slot = db.get_slot(slot_id)
    trainer = db.get_trainer(slot["trainer_id"])
    await callback.message.edit_text(
        f"🎉 Готово! Записал(а) тебя к <b>{esc(trainer['name'])}</b> "
        f"на <b>{fmt_slot(slot['slot_dt'])}</b>."
    )
    await callback.answer()
    try:
        await bot.send_message(
            slot["trainer_id"],
            f"🔔 <b>Новая запись!</b>\n{esc(client_name)} — {fmt_slot(slot['slot_dt'])}",
        )
    except Exception:
        logger.warning("Не удалось уведомить тренера")


@dp.message(F.text.in_({"🗓 Мои записи"}))
async def client_my_bookings(message: Message):
    rows = db.list_client_bookings(message.from_user.id)
    if not rows:
        await message.answer("Пока нет записей 🗓\nЖми «🔍 Записаться».")
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=f"❌ {fmt_slot(r['slot_dt'])} — {r['trainer_name']}",
                callback_data=f"unbook:{r['id']}",
            )]
            for r in rows
        ]
    )
    await message.answer("🗓 <b>Твои записи</b>\nНажми, чтобы отменить:", reply_markup=kb)


@dp.callback_query(F.data.startswith("unbook:"))
async def client_cancel_booking(callback: CallbackQuery):
    slot_id = int(callback.data.split(":")[1])
    slot = db.get_slot(slot_id)
    if not slot or slot["client_id"] != callback.from_user.id:
        await callback.answer("Не нашёл эту запись.", show_alert=True)
        return
    db.free_up_slot(slot_id)
    await callback.message.edit_text(f"🗑 Отменил запись на <b>{fmt_slot(slot['slot_dt'])}</b>.")
    await callback.answer()
    try:
        await bot.send_message(
            slot["trainer_id"],
            f"⚠️ {esc(callback.from_user.full_name)} отменил(а) запись на "
            f"<b>{fmt_slot(slot['slot_dt'])}</b>",
        )
    except Exception:
        logger.warning("Не удалось уведомить тренера")


# ---------- Напоминания ----------

async def send_reminders():
    now = datetime.now()

    win24_start = (now + timedelta(hours=23)).strftime("%Y-%m-%d %H:%M")
    win24_end = (now + timedelta(hours=25)).strftime("%Y-%m-%d %H:%M")
    for slot in db.slots_needing_reminder("reminder_24h_sent", win24_start, win24_end):
        try:
            await bot.send_message(
                slot["client_id"],
                f"⏰ Напоминаю: завтра в <b>{slot['slot_dt'][-5:]}</b> у тебя тренировка.",
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
                f"⏰ Через час у тебя тренировка (<b>{slot['slot_dt'][-5:]}</b>). Не забудь!",
            )
        except Exception:
            logger.warning("Не удалось отправить напоминание клиенту %s", slot["client_id"])
        db.mark_reminder_sent(slot["id"], "reminder_1h_sent")


async def main():
    if not BOT_TOKEN:
        raise SystemExit("Заполни BOT_TOKEN в файле .env")

    db.init_db()

    scheduler = AsyncIOScheduler()
    scheduler.add_job(send_reminders, "interval", minutes=5)
    scheduler.start()

    logger.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
