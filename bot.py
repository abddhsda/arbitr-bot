import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import Database
from arbitr_parser import ArbitrParser

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
db = Database()
parser = ArbitrParser()


class AddCompany(StatesGroup):
    waiting_for_inn = State()


def main_menu_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Добавить компанию", callback_data="add_company")
    kb.button(text="📋 Мой список", callback_data="my_list")
    kb.button(text="ℹ️ Статус бота", callback_data="status")
    kb.adjust(1)
    return kb.as_markup()


def back_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Назад", callback_data="back_to_menu")
    return kb.as_markup()


@dp.message(CommandStart())
async def cmd_start(message: Message):
    db.add_user(message.from_user.id)
    await message.answer(
        "👨‍⚖️ <b>АрбитрМонитор</b>\n\n"
        "Слежу за арбитражными делами 24/7. "
        "Как только появится новый иск или изменение — сразу пришлю уведомление.\n\n"
        "Выберите действие:",
        reply_markup=main_menu_kb(),
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "👨‍⚖️ <b>АрбитрМонитор</b>\n\nВыберите действие:",
        reply_markup=main_menu_kb(),
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "status")
async def status(callback: CallbackQuery):
    user_count = db.get_user_count()
    inn_count = db.get_total_inn_count()
    await callback.message.edit_text(
        f"✅ <b>Бот работает нормально</b>\n\n"
        f"👥 Пользователей: {user_count}\n"
        f"🏢 Компаний на мониторинге: {inn_count}\n"
        f"🔄 Проверка каждые 30 минут",
        reply_markup=back_kb(),
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "add_company")
async def add_company(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AddCompany.waiting_for_inn)
    await callback.message.edit_text(
        "🔢 Введите <b>ИНН</b> компании (10 или 12 цифр):",
        reply_markup=back_kb(),
        parse_mode="HTML"
    )


@dp.message(AddCompany.waiting_for_inn)
async def process_inn(message: Message, state: FSMContext):
    inn = message.text.strip().replace(" ", "")

    if not inn.isdigit() or len(inn) not in (10, 12):
        await message.answer(
            "❌ Неверный формат. ИНН должен содержать 10 или 12 цифр.\n\nПопробуйте снова:"
        )
        return

    await message.answer("⏳ Проверяю ИНН в системе Мой Арбитр...")

    company_name = await parser.get_company_name(inn)

    if not company_name:
        await message.answer(
            "⚠️ Не удалось найти компанию с таким ИНН. Проверьте и попробуйте снова.",
            reply_markup=back_kb()
        )
        await state.clear()
        return

    already_exists = db.inn_exists_for_user(message.from_user.id, inn)
    if already_exists:
        await message.answer(
            f"ℹ️ Компания <b>{company_name}</b> уже в вашем списке.",
            reply_markup=main_menu_kb(),
            parse_mode="HTML"
        )
        await state.clear()
        return

    # Сохраняем ИНН и делаем снимок текущего состояния дел
    db.add_inn(message.from_user.id, inn, company_name)
    cases = await parser.get_cases(inn)
    if cases:
    db.save_snapshot(inn, cases)

    await state.clear()
    await message.answer(
        f"✅ Компания <b>{company_name}</b> добавлена!\n\n"
        f"📂 Найдено дел: <b>{len(cases)}</b>\n\n"
        f"Буду уведомлять вас о любых изменениях.",
        reply_markup=main_menu_kb(),
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "my_list")
async def my_list(callback: CallbackQuery):
    companies = db.get_user_companies(callback.from_user.id)

    if not companies:
        await callback.message.edit_text(
            "📋 Ваш список пуст.\n\nДобавьте компанию по ИНН.",
            reply_markup=main_menu_kb()
        )
        return

    kb = InlineKeyboardBuilder()
    for inn, name in companies:
        kb.button(text=f"❌ {name}", callback_data=f"remove_{inn}")
    kb.button(text="◀️ Назад", callback_data="back_to_menu")
    kb.adjust(1)

    text = "📋 <b>Отслеживаемые компании:</b>\n\n"
    for i, (inn, name) in enumerate(companies, 1):
        text += f"{i}. {name}\n<code>{inn}</code>\n\n"
    text += "Нажмите на компанию, чтобы <b>удалить</b> из списка."

    await callback.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")


@dp.callback_query(F.data.startswith("remove_"))
async def remove_company(callback: CallbackQuery):
    inn = callback.data.split("_", 1)[1]
    name = db.get_company_name(inn)
    db.remove_inn(callback.from_user.id, inn)
    await callback.answer(f"Удалено: {name}", show_alert=True)

    # Показываем обновлённый список
    companies = db.get_user_companies(callback.from_user.id)
    if not companies:
        await callback.message.edit_text(
            "📋 Ваш список пуст.\n\nДобавьте компанию по ИНН.",
            reply_markup=main_menu_kb()
        )
        return

    kb = InlineKeyboardBuilder()
    for inn_, name_ in companies:
        kb.button(text=f"❌ {name_}", callback_data=f"remove_{inn_}")
    kb.button(text="◀️ Назад", callback_data="back_to_menu")
    kb.adjust(1)

    text = "📋 <b>Отслеживаемые компании:</b>\n\n"
    for i, (inn_, name_) in enumerate(companies, 1):
        text += f"{i}. {name_}\n<code>{inn_}</code>\n\n"
    text += "Нажмите на компанию, чтобы <b>удалить</b> из списка."
    await callback.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")


async def monitor_loop():
    """Фоновая задача: каждые 30 минут проверяет изменения по всем ИНН"""
    logger.info("Мониторинг запущен")
    while True:
        await asyncio.sleep(1800)  # 30 минут
        try:
            all_inns = db.get_all_inns()
            for inn, company_name in all_inns:
                logger.info(f"Проверяю {inn} ({company_name})")
                current_cases = await parser.get_cases(inn)
                changes = db.compare_and_update_snapshot(inn, current_cases)

                if changes:
                    users = db.get_users_for_inn(inn)
                    for user_id in users:
                        for change in changes:
                            text = format_change_message(company_name, inn, change)
                            try:
                                await bot.send_message(user_id, text, parse_mode="HTML")
                            except Exception as e:
                                logger.error(f"Не удалось отправить {user_id}: {e}")
        except Exception as e:
            logger.error(f"Ошибка мониторинга: {e}")


def format_change_message(company_name: str, inn: str, change: dict) -> str:
    """Форматирует уведомление об изменении"""
    event_type = change.get("type", "Изменение")
    case_number = change.get("case_number", "—")
    case_url = f"https://kad.arbitr.ru/Card/{change.get('case_id', '')}"
    amount = change.get("amount", "")
    court = change.get("court", "")
    plaintiff = change.get("plaintiff", "")
    defendant = change.get("defendant", "")
    detail = change.get("detail", "")

    msg = (
        f"⚖️ <b>{event_type}</b>\n\n"
        f"🏢 <b>Компания:</b> {company_name} ({inn})\n"
        f"📁 <b>Дело:</b> <a href='{case_url}'>{case_number}</a>\n"
    )
    if court:
        msg += f"🏛 <b>Суд:</b> {court}\n"
    if amount:
        msg += f"💰 <b>Сумма иска:</b> {amount}\n"
    if plaintiff:
        msg += f"👤 <b>Истец:</b> {plaintiff}\n"
    if defendant:
        msg += f"👤 <b>Ответчик:</b> {defendant}\n"
    if detail:
        msg += f"📝 <b>Детали:</b> {detail}\n"

    return msg


async def main():
    db.init()
    asyncio.create_task(monitor_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
