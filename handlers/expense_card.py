from aiogram.types import CallbackQuery

from database import get_category, get_expense
from keyboards import expense_detail_keyboard
from services import format_date, format_money
from ui import safe_edit


async def show_expense_card(callback: CallbackQuery, expense_id: int) -> bool:
    expense = await get_expense(expense_id)
    if not expense:
        await callback.answer("Трата не найдена", show_alert=True)
        return False

    category = await get_category(expense["category_id"])
    category_name = category["name"] if category else "—"
    text = (
        f"🧾 Трата #{expense['id']}\n"
        f"Дата: {format_date(expense['spent_on'])}\n"
        f"Категория: {category_name}\n"
        f"Сумма: {format_money(expense['amount'])} ₽"
    )
    await safe_edit(
        callback.message,
        text,
        expense_detail_keyboard(expense_id, expense["category_id"]),
    )
    return True
