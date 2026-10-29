import re
from datetime import date

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from access import resolve_budget, resolve_user
from database import (
    add_expense,
    delete_expense,
    get_categories,
    get_expense,
    update_expense,
)
from handlers.expense_card import show_expense_card
from keyboards import (
    CANCEL_TEXT,
    back_to_menu_keyboard,
    categories_keyboard,
    categories_reply_keyboard,
    remove_keyboard,
)
from services import format_date, format_money, parse_amount
from states import EditExpenseState, ExpenseState
from ui import safe_edit

router = Router()

# Число с необязательной валютой: «500», «450,50», «1 200 ₽»
AMOUNT_PATTERN = re.compile(r"^\s*\d[\d\s]*(?:[.,]\d{1,2})?\s*(?:₽|р\.?|руб\.?)?\s*$", re.IGNORECASE)


def _extract_amount(text: str | None) -> float | None:
    """Достаёт сумму из сообщения, отбрасывая валюту и пробелы."""
    if not text:
        return None
    cleaned = re.sub(r"[^\d.,]", "", text)
    return parse_amount(cleaned)



async def _expense_for_user(callback: CallbackQuery, expense_id: int):
    user = await resolve_user(callback)
    budget = await resolve_budget(user)
    expense = await get_expense(expense_id)
    if not expense or not budget or expense["budget_id"] != budget["id"]:
        await callback.answer("Трата не найдена", show_alert=True)
        return None, None
    return expense, budget


async def _ask_category(
    message: Message, state: FSMContext, budget_id: int, amount: float
) -> None:
    """Показывает категории reply-клавиатурой у поля ввода."""
    categories = await get_categories(budget_id)
    if not categories:
        await state.clear()
        await message.answer(
            "Сначала создайте хотя бы одну категорию в разделе «📂 Категории».",
            reply_markup=remove_keyboard(),
        )
        return

    await state.update_data(budget_id=budget_id, amount=amount)
    await state.set_state(ExpenseState.waiting_category)
    await message.answer(
        f"Сумма: {format_money(amount)} ₽\nВыберите категорию:",
        reply_markup=categories_reply_keyboard(categories),
    )


async def _save_expense(
    message: Message, state: FSMContext, budget_id: int, category_id: int, amount: float
) -> None:
    user = await resolve_user(message)
    spent_on = date.today()
    await add_expense(budget_id, category_id, user["id"], amount, spent_on)
    await state.clear()
    await message.answer(
        f"✅ Трата записана\n"
        f"Сумма: {format_money(amount)} ₽\n"
        f"Дата: {format_date(spent_on)}\n\n"
        "Введите следующую сумму или отправьте /start для меню.",
        reply_markup=remove_keyboard(),
    )


@router.callback_query(F.data == "add_expense")
async def start_add_expense(callback: CallbackQuery, state: FSMContext) -> None:
    user = await resolve_user(callback)
    budget = await resolve_budget(user)
    if not budget:
        await callback.answer("Сначала отправьте /start", show_alert=True)
        return

    categories = await get_categories(budget["id"])
    if not categories:
        await safe_edit(
            callback.message,
            "Сначала создайте хотя бы одну категорию в разделе «📂 Категории».",
            back_to_menu_keyboard(),
        )
        await callback.answer()
        return

    await state.set_state(ExpenseState.waiting_amount)
    await state.update_data(budget_id=budget["id"])
    await safe_edit(callback.message, "Введите сумму траты (например, 450 или 450,50):")
    await callback.answer()


@router.message(ExpenseState.waiting_amount)
async def process_amount(message: Message, state: FSMContext) -> None:
    amount = _extract_amount(message.text)
    if amount is None:
        await message.answer("Введите сумму больше нуля, например 450 или 450,50.")
        return

    data = await state.get_data()
    budget_id = data.get("budget_id")
    if not budget_id:
        await state.clear()
        await message.answer("Сессия устарела, отправьте /start.")
        return

    await _ask_category(message, state, budget_id, amount)


@router.message(StateFilter(None), F.text.regexp(AMOUNT_PATTERN))
async def quick_add_expense(message: Message, state: FSMContext) -> None:
    """Любое сообщение с числом — это новая трата."""
    amount = _extract_amount(message.text)
    if amount is None:
        return

    user = await resolve_user(message)
    budget = await resolve_budget(user)
    if not budget:
        await message.answer("Отправьте /start, чтобы начать.")
        return

    await _ask_category(message, state, budget["id"], amount)


@router.message(ExpenseState.waiting_category, F.text)
async def process_category_text(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    data = await state.get_data()
    budget_id = data.get("budget_id")
    amount = data.get("amount")
    if not budget_id or amount is None:
        await state.clear()
        await message.answer(
            "Сессия устарела, отправьте /start.", reply_markup=remove_keyboard()
        )
        return

    if text.casefold() == CANCEL_TEXT.casefold():
        await state.clear()
        await message.answer("Отменено.", reply_markup=remove_keyboard())
        return

    # Вместо категории ввели новое число — обновляем сумму
    if AMOUNT_PATTERN.match(text):
        new_amount = _extract_amount(text)
        if new_amount is not None:
            await _ask_category(message, state, budget_id, new_amount)
            return

    categories = await get_categories(budget_id)
    target = text.casefold()
    category = next(
        (item for item in categories if (item["name"] or "").casefold() == target), None
    )
    if not category:
        await message.answer(
            "Выберите категорию кнопкой ниже или нажмите «❌ Отмена».",
            reply_markup=categories_reply_keyboard(categories),
        )
        return

    await _save_expense(message, state, budget_id, category["id"], amount)


@router.callback_query(ExpenseState.waiting_category, F.data.startswith("pick_cat_"))
async def process_category_callback(callback: CallbackQuery, state: FSMContext) -> None:
    """Совместимость со старыми inline-кнопками в истории чата."""
    category_id = int(callback.data.removeprefix("pick_cat_"))
    data = await state.get_data()
    amount = data.get("amount")
    budget_id = data.get("budget_id")
    if amount is None or not budget_id:
        await state.clear()
        await callback.answer("Сессия устарела, отправьте /start", show_alert=True)
        return

    user = await resolve_user(callback)
    spent_on = date.today()
    await add_expense(budget_id, category_id, user["id"], amount, spent_on)
    await state.clear()
    await safe_edit(
        callback.message,
        f"✅ Трата записана\nСумма: {format_money(amount)} ₽\nДата: {format_date(spent_on)}",
        back_to_menu_keyboard(),
    )
    await callback.answer("Записано")


@router.callback_query(F.data.startswith("del_exp_"))
async def delete_expense_handler(callback: CallbackQuery, state: FSMContext) -> None:
    expense_id = int(callback.data.removeprefix("del_exp_"))
    expense, _ = await _expense_for_user(callback, expense_id)
    if not expense:
        return

    await state.clear()
    await delete_expense(expense_id)
    await safe_edit(callback.message, "✅ Трата удалена.", back_to_menu_keyboard())
    await callback.answer("Удалено")


@router.callback_query(F.data.startswith("edit_amount_"))
async def edit_amount_start(callback: CallbackQuery, state: FSMContext) -> None:
    expense_id = int(callback.data.removeprefix("edit_amount_"))
    expense, _ = await _expense_for_user(callback, expense_id)
    if not expense:
        return

    await state.set_state(EditExpenseState.waiting_amount)
    await state.update_data(expense_id=expense_id)
    await safe_edit(
        callback.message,
        f"Текущая сумма: {format_money(expense['amount'])} ₽\nВведите новую сумму:",
    )
    await callback.answer()


@router.message(EditExpenseState.waiting_amount)
async def edit_amount_process(message: Message, state: FSMContext) -> None:
    amount = _extract_amount(message.text)
    if amount is None:
        await message.answer("Введите сумму больше нуля, например 450 или 450,50.")
        return

    data = await state.get_data()
    expense_id = data.get("expense_id")
    if not expense_id:
        await state.clear()
        await message.answer("Сессия устарела, отправьте /start.")
        return

    await update_expense(expense_id, "amount", amount)
    await state.clear()
    await message.answer(
        f"✅ Сумма обновлена: {format_money(amount)} ₽", reply_markup=back_to_menu_keyboard()
    )


@router.callback_query(F.data.startswith("edit_cat_"))
async def edit_category_start(callback: CallbackQuery, state: FSMContext) -> None:
    expense_id = int(callback.data.removeprefix("edit_cat_"))
    expense, budget = await _expense_for_user(callback, expense_id)
    if not expense:
        return

    categories = await get_categories(budget["id"])
    await state.update_data(expense_id=expense_id)
    await safe_edit(
        callback.message,
        "Выберите новую категорию:",
        categories_keyboard(categories, f"ecat_{expense_id}_"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ecat_"))
async def edit_category_apply(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split("_")
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await callback.answer("Кнопка устарела", show_alert=True)
        return

    expense_id = int(parts[1])
    category_id = int(parts[2])
    expense, _ = await _expense_for_user(callback, expense_id)
    if not expense:
        return

    await update_expense(expense_id, "category_id", category_id)
    await state.clear()
    await show_expense_card(callback, expense_id)
    await callback.answer("Категория изменена")


@router.message(StateFilter(None), F.text)
async def fallback_text(message: Message) -> None:
    """Ответ на текст вне сценариев: подсказываем формат ввода."""
    text = (message.text or "").strip()
    user = await resolve_user(message)
    budget = await resolve_budget(user)
    if budget and text:
        categories = await get_categories(budget["id"])
        target = text.casefold()
        if any((item["name"] or "").casefold() == target for item in categories):
            await message.answer(
                f"Категория «{text}».\nВведите сумму траты, например 450:",
                reply_markup=remove_keyboard(),
            )
            return

    await message.answer(
        "Введите сумму (например 450) — и я предложу категории.\n"
        "Или отправьте /start для меню.",
        reply_markup=remove_keyboard(),
    )
