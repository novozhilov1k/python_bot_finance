from datetime import date

from aiogram import F, Router
from aiogram.types import CallbackQuery

from access import resolve_budget, resolve_user
from database import get_categories, get_category, get_expenses_by_category
from handlers.expense_card import show_expense_card
from keyboards import back_to_menu_keyboard, expense_list_keyboard, stats_keyboard
from reports import build_period_stats
from services import format_money, format_period, get_period_end, get_period_start
from ui import safe_edit

router = Router()


@router.callback_query(F.data == "stats")
async def show_stats(callback: CallbackQuery) -> None:
    user = await resolve_user(callback)
    budget = await resolve_budget(user)
    if not budget:
        await callback.answer("Сначала отправьте /start", show_alert=True)
        return

    period_start = get_period_start(date.today(), budget["month_start_day"] or 10)
    text = await build_period_stats(budget["id"], period_start)
    categories = await get_categories(budget["id"])
    await safe_edit(callback.message, text, stats_keyboard(categories))
    await callback.answer()


@router.callback_query(F.data.startswith("stats_cat_"))
async def show_category_expenses(callback: CallbackQuery) -> None:
    category_id = int(callback.data.removeprefix("stats_cat_"))
    user = await resolve_user(callback)
    budget = await resolve_budget(user)
    if not budget:
        await callback.answer("Сначала отправьте /start", show_alert=True)
        return

    period_start = get_period_start(date.today(), budget["month_start_day"] or 10)
    period_end = get_period_end(period_start)
    expenses = await get_expenses_by_category(
        budget["id"], category_id, period_start.isoformat(), period_end.isoformat()
    )
    category = await get_category(category_id)
    name = category["name"] if category else "Категория"

    if not expenses:
        await safe_edit(
            callback.message,
            f"В категории «{name}» за период {format_period(period_start)} трат нет.",
            back_to_menu_keyboard(),
        )
        await callback.answer()
        return

    total = sum(item["amount"] for item in expenses)
    text = (
        f"🧾 {name} за {format_period(period_start)}\n"
        f"Трат: {len(expenses)}, сумма: {format_money(total)} ₽\n\n"
        "Нажмите на трату, чтобы открыть карточку."
    )
    await safe_edit(callback.message, text, expense_list_keyboard(expenses))
    await callback.answer()


@router.callback_query(F.data.startswith("view_exp_"))
async def show_expense(callback: CallbackQuery) -> None:
    expense_id = int(callback.data.removeprefix("view_exp_"))
    if await show_expense_card(callback, expense_id):
        await callback.answer()
