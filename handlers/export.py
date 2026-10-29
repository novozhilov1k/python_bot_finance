from datetime import date
from io import BytesIO

from aiogram import F, Router
from aiogram.types import BufferedInputFile, CallbackQuery
from openpyxl import Workbook
from openpyxl.styles import Font

from access import resolve_budget, resolve_user
from database import get_expenses_for_period
from keyboards import back_to_menu_keyboard, export_keyboard
from services import (
    format_date,
    format_period,
    get_period_end,
    get_period_start,
    get_previous_period_start,
)
from ui import safe_edit

router = Router()


@router.callback_query(F.data == "export")
async def export_menu(callback: CallbackQuery) -> None:
    await safe_edit(
        callback.message,
        "📤 Экспорт данных в Excel\nВыберите период:",
        export_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "export_current")
async def export_current(callback: CallbackQuery) -> None:
    user = await resolve_user(callback)
    budget = await resolve_budget(user)
    if not budget:
        await callback.answer("Сначала отправьте /start", show_alert=True)
        return

    period_start = get_period_start(date.today(), budget["month_start_day"] or 10)
    await _send_excel(callback, budget, period_start)


@router.callback_query(F.data == "export_prev")
async def export_prev(callback: CallbackQuery) -> None:
    user = await resolve_user(callback)
    budget = await resolve_budget(user)
    if not budget:
        await callback.answer("Сначала отправьте /start", show_alert=True)
        return

    current_start = get_period_start(date.today(), budget["month_start_day"] or 10)
    previous_start = get_previous_period_start(current_start, budget["month_start_day"] or 10)
    await _send_excel(callback, budget, previous_start)


async def _send_excel(callback: CallbackQuery, budget, period_start: date) -> None:
    period_end = get_period_end(period_start)
    expenses = await get_expenses_for_period(
        budget["id"], period_start.isoformat(), period_end.isoformat()
    )
    if not expenses:
        await safe_edit(
            callback.message,
            f"За период {format_period(period_start)} трат нет.",
            back_to_menu_keyboard(),
        )
        await callback.answer()
        return

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = period_start.strftime("%m.%Y")
    sheet.append(["Дата", "Категория", "Сумма, ₽"])

    total = 0.0
    for expense in expenses:
        sheet.append([format_date(expense["spent_on"]), expense["category_name"], expense["amount"]])
        total += float(expense["amount"])

    sheet.append([])
    sheet.append(["", "Итого", round(total, 2)])
    for cell in sheet[sheet.max_row]:
        cell.font = Font(bold=True)

    sheet.column_dimensions["A"].width = 14
    sheet.column_dimensions["B"].width = 28
    sheet.column_dimensions["C"].width = 14

    buffer = BytesIO()
    workbook.save(buffer)
    document = BufferedInputFile(
        buffer.getvalue(), filename=f"finance_{period_start.strftime('%Y_%m')}.xlsx"
    )
    await callback.message.answer_document(
        document, caption=f"📤 Экспорт за {format_period(period_start)}"
    )
    await callback.answer("Файл отправлен")
