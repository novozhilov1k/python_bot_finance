from datetime import date

from aiogram import F, Router
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
from keyboards import back_to_menu_keyboard, categories_keyboard
from services import format_date, format_money, parse_amount
from states import EditExpenseState, ExpenseState
from ui import safe_edit

router = Router()


async def _expense_for_user(callback: CallbackQuery, expense_id: int):
    user = await resolve_user(callback)
    budget = await resolve_budget(user)
    expense = await get_expense(expense_id)
    if not expense or not budget or expense["budget_id"] != budget["id"]:
        await callback.answer("Трата не найдена", show_alert=True)
        return None, None
    return expense, budget


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
    amount = parse_amount(message.text)
    if amount is None:
        await message.answer("Введите сумму больше нуля, например 450 или 450,50.")
        return

    data = await state.get_data()
    budget_id = data.get("budget_id")
    if not budget_id:
        await state.clear()
        await message.answer("Сессия устарела, отправьте /start.")
        return

    categories = await get_categories(budget_id)
    if not categories:
        await state.clear()
        await message.answer("Сначала создайте категорию.", reply_markup=back_to_menu_keyboard())
        return

    await state.update_data(amount=amount)
    await state.set_state(ExpenseState.waiting_category)
    await message.answer(
        f"Сумма: {format_money(amount)} ₽\nВыберите категорию:",
        reply_markup=categories_keyboard(categories, "pick_cat_"),
    )


@router.callback_query(ExpenseState.waiting_category, F.data.startswith("pick_cat_"))
async def process_category(callback: CallbackQuery, state: FSMContext) -> None:
    category_id = int(callback.data.removeprefix("pick_cat_"))
    data = await state.get_data()
    amount = data.get("amount")
    budget_id = data.get("budget_id")
    if amount is None or not budget_id:
        await state.clear()
        await callback.answer("Сессия устарела, отправьте /start", show_alert=True)
        return

    user = await resolve_user(callback)
    spent_on = date.today().isoformat()
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
    amount = parse_amount(message.text)
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
