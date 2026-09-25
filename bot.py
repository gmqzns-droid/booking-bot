"""
Телеграм-бот для записи клиентов к тренерам. v4 — у каждого клиента «свой» тренер.

Тренер регистрируется командой /trainer и получает персональную ссылку («🔗 Моя ссылка»).
Клиент, перешедший по этой ссылке, сразу привязывается к этому тренеру — никаких меню
«я тренер / я клиент» и выбора из списка тренеров, у клиента всегда одно простое меню.
Тренер добавляет свободное время вручную или сразу на много недель вперёд («🔁 Еженедельно»).
Бот сам шлёт клиенту напоминания за 24 часа и за 1 час до тренировки, а после записи
тренер и клиент видят контакты друг друга.
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
from aiogram.filters import Command, CommandObject, StateFilter
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
RECUR_WEEKS = 8  # на сколько недель вперёд генерировать повторяющееся расписание
MSK = ZoneInfo("Europe/Moscow")


def now_msk() -> datetime:
    """Текущее время по Москве — сервер бота крутится не в московском часовом поясе,
    поэтому весь расчёт 'сегодня/сейчас' идёт через эту функцию, а не datetime.now()."""
    return datetime.now(MSK)


# ---------- FSM состояния ----------

class TrainerOnboarding(StatesGroup):
    waiting_name = State()
    waiting_specialty = State()


class AddSlot(StatesGroup):
    waiting_custom_time = State()


class RecurSchedule(StatesGroup):
    picking_days = State()
    waiting_custom_time = State()


class EditProfile(StatesGroup):
    waiting_name = State()
    waiting_specialty = State()


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
            [KeyboardButton(text="➕ Добавить время"), KeyboardButton(text="🔁 Еженедельно")],
            [KeyboardButton(text="📋 Мои записи"), KeyboardButton(text="⚙️ Профиль")],
            [KeyboardButton(text="🔗 Моя ссылка")],
        ],
        resize_keyboard=True,
    )


def client_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔍 Записаться")],
            [KeyboardButton(text="🗓 Мои записи")],
        ],
        resize_keyboard=True,
    )


def next_14_days() -> list[str]:
    return [(now_msk() + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(14)]


def weekday_picker_kb(selected: list[int]) -> InlineKeyboardMarkup:
    day_buttons = [
        InlineKeyboardButton(
            text=(f"✅ {DAYS_RU[i]}" if i in selected else DAYS_RU[i]),
            callback_data=f"recday:{i}",
        )
        for i in range(7)
    ]
    rows = [day_buttons[0:4], day_buttons[4:7]]
    rows.append([
        InlineKeyboardButton(text="➡️ Дальше", callback_data="recnext"),
        InlineKeyboardButton(text="✖️ Отмена", callback_data="reccancel"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def generate_recurring_slots(trainer_id: int, selected_days: list[int], hh: int, mm: int) -> tuple[int, int]:
    """Создаёт конкретные слоты на RECUR_WEEKS недель вперёд для выбранных дней недели."""
    today = now_msk().date()
    added = skipped = 0
    for wd in selected_days:
        delta = (wd - today.weekday()) % 7
        base = today + timedelta(days=delta)
        for week in range(RECUR_WEEKS):
            d = base + timedelta(weeks=week)
            slot_dt = f"{d:%Y-%m-%d} {hh:02d}:{mm:02d}"
            if db.add_slot(trainer_id, slot_dt):
                added += 1
            else:
                skipped += 1
    return added, skipped


def recur_summary(selected_days: list[int], hh: int, mm: int, added: int, skipped: int) -> str:
    days_label = ", ".join(DAYS_RU[i] for i in selected_days)
    text = (
        f"✅ Добавил <b>{added}</b> тренировок: {days_label} в <b>{hh:02d}:{mm:02d}</b> "
        f"на ближайшие {RECUR_WEEKS} недель."
    )
    if skipped:
        text += f"\n<i>({skipped} слотов уже были добавлены ранее)</i>"
    return text


async def get_contact_line(trainer_id: int) -> str:
    try:
        chat = await bot.get_chat(trainer_id)
        if chat.username:
            return f"\n💬 Связаться с тренером: @{chat.username}"
    except Exception:
        logger.warning("Не удалось получить контакт тренера %s", trainer_id)
    return ""


# ---------- /start, /help и регистрация тренера ----------

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext, command: CommandObject):
    await state.clear()

    trainer = db.get_trainer(message.from_user.id)
    if trainer:
        await message.answer(
            f"С возвращением, <b>{esc(trainer['name'])}</b>! 👋\n"
            f"Это твой кабинет тренера — отсюда управляешь расписанием.\n"
            f"Команда /help — если нужна подсказка.",
            reply_markup=trainer_menu(),
        )
        return

    # Клиент: определяем "своего" тренера — по персональной ссылке,
    # по уже существующей привязке или по прошлым записям.
    trainer_id = None
    payload = (command.args or "").strip()
    if payload.isdigit() and db.get_trainer(int(payload)):
        trainer_id = int(payload)
    if trainer_id is None:
        trainer_id = db.get_client_trainer(message.from_user.id)
    if trainer_id is None:
        bookings = db.list_client_bookings(message.from_user.id)
        if bookings:
            trainer_id = bookings[0]["trainer_id"]
        elif len(db.list_trainers()) == 1:
            trainer_id = db.list_trainers()[0]["id"]

    if trainer_id is None:
        await message.answer(
            "👋 <b>Привет!</b>\n\n"
            "Похоже, у тебя нет ссылки от тренера — попроси у него персональную ссылку "
            "на этого бота, и всё будет готово за секунду.\n\n"
            "Если ты сам тренер и хочешь завести здесь расписание — напиши /trainer."
        )
        return

    db.link_client(message.from_user.id, trainer_id, message.from_user.full_name, message.from_user.username)
    bound_trainer = db.get_trainer(trainer_id)
    await message.answer(
        f"👋 <b>Привет!</b> Это бот записи к тренеру <b>{esc(bound_trainer['name'])}</b>.\n"
        f"Выбирай, что нужно 👇 (подсказка — команда /help)",
        reply_markup=client_menu(),
    )


@dp.message(Command("trainer"))
async def cmd_become_trainer(message: Message, state: FSMContext):
    trainer = db.get_trainer(message.from_user.id)
    if trainer:
        await message.answer(
            f"Ты уже тренер, <b>{esc(trainer['name'])}</b> 🙂", reply_markup=trainer_menu()
        )
        return
    await state.clear()
    await message.answer("🧑‍🏫 Заводим тебе кабинет тренера. Как тебя подписывать клиентам? Напиши имя.")
    await state.set_state(TrainerOnboarding.waiting_name)


@dp.message(Command("help"))
async def cmd_help(message: Message):
    trainer = db.get_trainer(message.from_user.id)
    if trainer:
        await message.answer(
            "🧑‍🏫 <b>Как пользоваться боту-тренеру</b>\n\n"
            "➕ <b>Добавить время</b> — одна тренировка на конкретный день\n"
            "🔁 <b>Еженедельно</b> — повторяющееся расписание (например, Пн/Ср/Пт в одно время) "
            f"сразу на {RECUR_WEEKS} недель вперёд\n"
            "📋 <b>Мои записи</b> — все слоты, занятые и свободные; нажми, чтобы отменить\n"
            "⚙️ <b>Профиль</b> — изменить имя или специальность\n"
            "🔗 <b>Моя ссылка</b> — персональная ссылка для клиентов\n\n"
            "Когда клиент бронирует время, тебе приходит уведомление с его контактом.",
            reply_markup=trainer_menu(),
        )
    else:
        await message.answer(
            "🙋 <b>Как пользоваться ботом</b>\n\n"
            "🔍 <b>Записаться</b> — выбрать день и время у своего тренера\n"
            "🗓 <b>Мои записи</b> — твои записи; нажми, чтобы отменить\n\n"
            "Бот сам напомнит о тренировке за 24 часа и за час до неё.\n"
            "Если ты сам тренер — напиши /trainer.",
        )


@dp.message(StateFilter(TrainerOnboarding.waiting_name))
async def onb_name(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("🤔 Пришли имя текстом.")
        return
    await state.update_data(name=message.text.strip())
    await state.set_state(TrainerOnboarding.waiting_specialty)
    await message.answer("👍 А чем занимаешься? Например: плавание, теннис, репетиторство…")


@dp.message(StateFilter(TrainerOnboarding.waiting_specialty))
async def onb_specialty(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("🤔 Пришли специальность текстом.")
        return
    data = await state.get_data()
    db.register_trainer(message.from_user.id, data["name"], message.text.strip())
    await state.clear()
    await message.answer(
        f"🎉 Готово, <b>{esc(data['name'])}</b>! Профиль тренера создан.\n"
        f"1️⃣ Добавь свободное время — «➕ Добавить время» или сразу «🔁 Еженедельно».\n"
        f"2️⃣ Возьми «🔗 Моя ссылка» и отправь её своим клиентам — они сразу попадут "
        f"в твоё расписание, без лишних меню.",
        reply_markup=trainer_menu(),
    )


# ---------- Тренер: персональная ссылка для клиентов ----------

@dp.message(F.text == "🔗 Моя ссылка")
async def trainer_link(message: Message):
    trainer = db.get_trainer(message.from_user.id)
    if not trainer:
        return
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start={message.from_user.id}"
    await message.answer(
        f"🔗 <b>Твоя персональная ссылка для клиентов:</b>\n{link}\n\n"
        f"Кто перейдёт по ней — сразу попадёт в твоё расписание, выбирать тренера не нужно."
    )


# ---------- Тренер: профиль ----------

@dp.message(F.text == "⚙️ Профиль")
async def trainer_profile(message: Message):
    trainer = db.get_trainer(message.from_user.id)
    if not trainer:
        return
    specialty = trainer["specialty"] or "не указана"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить имя", callback_data="editname")],
            [InlineKeyboardButton(text="✏️ Изменить специальность", callback_data="editspecialty")],
        ]
    )
    await message.answer(
        f"⚙️ <b>Твой профиль</b>\n"
        f"Имя: <b>{esc(trainer['name'])}</b>\n"
        f"Специальность: <b>{esc(specialty)}</b>",
        reply_markup=kb,
    )


@dp.callback_query(F.data == "editname")
async def edit_name_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(EditProfile.waiting_name)
    await callback.message.edit_text("✏️ Напиши новое имя:")
    await callback.answer()


@dp.message(StateFilter(EditProfile.waiting_name))
async def edit_name_finish(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("🤔 Пришли имя текстом.")
        return
    new_name = message.text.strip()
    db.update_trainer_profile(message.from_user.id, name=new_name)
    await state.clear()
    await message.answer(f"✅ Имя обновлено: <b>{esc(new_name)}</b>", reply_markup=trainer_menu())


@dp.callback_query(F.data == "editspecialty")
async def edit_specialty_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(EditProfile.waiting_specialty)
    await callback.message.edit_text("✏️ Напиши новую специальность:")
    await callback.answer()


@dp.message(StateFilter(EditProfile.waiting_specialty))
async def edit_specialty_finish(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("🤔 Пришли специальность текстом.")
        return
    new_specialty = message.text.strip()
    db.update_trainer_profile(message.from_user.id, specialty=new_specialty)
    await state.clear()
    await message.answer(f"✅ Специальность обновлена: <b>{esc(new_specialty)}</b>", reply_markup=trainer_menu())


# ---------- Тренер: добавление одного слота ----------

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
    if not message.text:
        await message.answer("🤔 Пришли время текстом, формат ЧЧ:ММ, например 13:30")
        return
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


# ---------- Тренер: повторяющееся расписание ----------

@dp.message(F.text == "🔁 Еженедельно")
async def recur_start(message: Message, state: FSMContext):
    if not db.is_trainer(message.from_user.id):
        return
    await state.set_state(RecurSchedule.picking_days)
    await state.update_data(selected_days=[])
    await message.answer(
        "🔁 <b>Еженедельное расписание</b>\n"
        "Выбери дни недели, когда у тебя тренировки (можно несколько), потом жми «Дальше»:",
        reply_markup=weekday_picker_kb([]),
    )


@dp.callback_query(F.data.startswith("recday:"), StateFilter(RecurSchedule.picking_days))
async def recur_toggle_day(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data.split(":")[1])
    data = await state.get_data()
    selected = set(data.get("selected_days", []))
    if idx in selected:
        selected.discard(idx)
    else:
        selected.add(idx)
    selected = sorted(selected)
    await state.update_data(selected_days=selected)
    await callback.message.edit_reply_markup(reply_markup=weekday_picker_kb(selected))
    await callback.answer()


@dp.callback_query(F.data == "reccancel")
async def recur_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Отменено. Можно начать заново через «🔁 Еженедельно».")
    await callback.answer()


@dp.callback_query(F.data == "recnext", StateFilter(RecurSchedule.picking_days))
async def recur_pick_time(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected = data.get("selected_days", [])
    if not selected:
        await callback.answer("Выбери хотя бы один день.", show_alert=True)
        return
    buttons = [InlineKeyboardButton(text=t, callback_data=f"rectime:{t}") for t in QUICK_TIMES]
    rows = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton(text="✏️ Своё время", callback_data="rectcustom")])
    rows.append([InlineKeyboardButton(text="✖️ Отмена", callback_data="reccancel")])
    days_label = ", ".join(DAYS_RU[i] for i in selected)
    await callback.message.edit_text(
        f"🔁 <b>{days_label}</b> — во сколько?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("rectime:"))
async def recur_finish(callback: CallbackQuery, state: FSMContext):
    time_str = callback.data.split(":", 1)[1]
    hh, mm = map(int, time_str.split(":"))
    data = await state.get_data()
    selected = data.get("selected_days", [])
    await state.clear()
    if not selected:
        await callback.answer("Что-то пошло не так, начни заново.", show_alert=True)
        return
    added, skipped = generate_recurring_slots(callback.from_user.id, selected, hh, mm)
    await callback.message.edit_text(recur_summary(selected, hh, mm, added, skipped))
    await callback.answer()


@dp.callback_query(F.data == "rectcustom")
async def recur_custom_time_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(RecurSchedule.waiting_custom_time)
    await callback.message.edit_text("✏️ Напиши время в формате ЧЧ:ММ, например <b>13:30</b>")
    await callback.answer()


@dp.message(StateFilter(RecurSchedule.waiting_custom_time))
async def recur_custom_time_finish(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("🤔 Пришли время текстом, формат ЧЧ:ММ, например 13:30")
        return
    text = message.text.strip()
    try:
        hh, mm = map(int, text.split(":"))
        assert 0 <= hh < 24 and 0 <= mm < 60
    except Exception:
        await message.answer("🤔 Не понял время. Формат ЧЧ:ММ, например <b>13:30</b>")
        return
    data = await state.get_data()
    selected = data.get("selected_days", [])
    await state.clear()
    if not selected:
        await message.answer("Что-то пошло не так, начни заново через «🔁 Еженедельно».", reply_markup=trainer_menu())
        return
    added, skipped = generate_recurring_slots(message.from_user.id, selected, hh, mm)
    await message.answer(recur_summary(selected, hh, mm, added, skipped), reply_markup=trainer_menu())


# ---------- Тренер: мои записи ----------

@dp.message(F.text == "📋 Мои записи")
async def trainer_my_slots(message: Message):
    if not db.is_trainer(message.from_user.id):
        return
    rows = db.list_all_upcoming(message.from_user.id)
    if not rows:
        await message.answer(
            "Пока нет ни одного слота 🗓\nДобавь через «➕ Добавить время» или «🔁 Еженедельно»."
        )
        return
    kb_rows = []
    for r in rows:
        if r["status"] == "booked":
            contact = f" (@{r['client_username']})" if r["client_username"] else ""
            label = f"🔴 {fmt_slot(r['slot_dt'])} — {r['client_name']}{contact}"
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


# ---------- Клиент: запись к своему тренеру ----------

@dp.message(F.text == "🔍 Записаться")
async def client_book(message: Message):
    trainer_id = db.get_client_trainer(message.from_user.id)
    if not trainer_id or not db.get_trainer(trainer_id):
        await message.answer("Не нашёл твоего тренера 🤔 Напиши /start ещё раз по ссылке от тренера.")
        return
    await show_days(message.chat.id, trainer_id)


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
    client_username = callback.from_user.username
    ok = db.book_slot(slot_id, callback.from_user.id, client_name, client_username)
    if not ok:
        await callback.answer("Увы, этот слот уже заняли.", show_alert=True)
        return
    slot = db.get_slot(slot_id)
    trainer = db.get_trainer(slot["trainer_id"])
    trainer_contact = await get_contact_line(slot["trainer_id"])
    await callback.message.edit_text(
        f"🎉 Готово! Записал(а) тебя к <b>{esc(trainer['name'])}</b> "
        f"на <b>{fmt_slot(slot['slot_dt'])}</b>.{trainer_contact}"
    )
    await callback.answer()
    client_contact = f" (@{esc(client_username)})" if client_username else ""
    try:
        await bot.send_message(
            slot["trainer_id"],
            f"🔔 <b>Новая запись!</b>\n{esc(client_name)}{client_contact} — {fmt_slot(slot['slot_dt'])}",
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
    now = now_msk()

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
