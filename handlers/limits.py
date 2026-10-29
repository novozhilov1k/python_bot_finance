from datetime import date

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from access import resolve_budget, resolve_user
from database import (
    confirm_period,
    copy_limits,
    get_budget,
    get_categories,
    get_limits_for_period,
    is_budget_member,
    set_active_budget,
    set_limit,
)
from keyboards import back_to_menu_keyboard, limit_targets_keyboard, limits_keyboard
from services import (
    format_money,
    format_period,
    get_period_start,
    get_previous_period_start,
    parse_amount,
)
from states import LimitState
from ui import safe_edit

router = Router()


async def _limits_payload(budget):
    period_start = get_period_start(date.today(), budget["month_start_day"] or 10)
    limits = await get_limits_for_period(budget["id"], period_start.isoformat())
    categories = await get_categories(budget["id"])

    lines = [f"🎯 Лимиты на период {format_period(period_start)}", ""]
    if not limits:
        lines.append("Лимитов пока нет.")
        lines.append("Можно задать лимит по категории и один общий лимит на все траты.")
    else:
        names = {category["id"]: category["name"] for category in categories}
        for limit in limits:
            if limit["category_id"] is None:
                lines.append(f"Общий лимит: {format_money(limit['amount'])} ₽")
            else:
                name = names.get(limit["category_id"], "удалённая категория")
                lines.append(f"{name}: {format_money(limit['amount'])} ₽")

    return "\n".join(lines), limits_keyboard(limits, categories)


@router.callback_query(F.data == "limits")
async def show_limits(callback: CallbackQuery, state: FSMContext) -> None:
    user = await resolve_user(callback)
    budget = await resolve_budget(user)
    if not budget:
        await callback.answer("Сначала отправьте /start", show_alert=True)
        return

    await state.clear()
    text, markup = await _limits_payload(budget)
    await safe_edit(callback.message, text, markup)
    await callback.answer()


@router.callback_query(F.data == "lim_add")
async def add_limit_start(callback: CallbackQuery, state: FSMContext) -> None:
    user = await resolve_user(callback)
    budget = await resolve_budget(user)
    if not budget:
        await callback.answer("Сначала отправьте /start", show_alert=True)
        return

    categories = await get_categories(budget["id"])
    await safe_edit(
        callback.message,
        "На что установить лимит?",
        limit_targets_keyboard(categories),
    )
    await callback.answer()


@router.callback_query(F.data == "lim_cat_general")
async def add_general_limit(callback: CallbackQuery, state: FSMContext) -> None:
    await _start_limit_input(callback, state, category_id=None)


@router.callback_query(F.data.startswith("lim_cat_"))
async def add_category_limit(callback: CallbackQuery, state: FSMContext) -> None:
    category_id = int(callback.data.removeprefix("lim_cat_"))
    await _start_limit_input(callback, state, category_id=category_id)


@router.callback_query(F.data == "lim_edit_general")
async def edit_general_limit(callback: CallbackQuery, state: FSMContext) -> None:
    await _start_limit_input(callback, state, category_id=None)


@router.callback_query(F.data.startswith("lim_edit_"))
async def edit_category_limit(callback: CallbackQuery, state: FSMContext) -> None:
    category_id = int(callback.data.removeprefix("lim_edit_"))
    await _start_limit_input(callback, state, category_id=category_id)


async def _start_limit_input(callback: CallbackQuery, state: FSMContext, category_id) -> None:
    user = await resolve_user(callback)
    budget = await resolve_budget(user)
    if not budget:
        await callback.answer("Сначала отправьте /start", show_alert=True)
        return

    period_start = get_period_start(date.today(), budget["month_start_day"] or 10)
    await state.set_state(LimitState.waiting_amount)
    await state.update_data(
        budget_id=budget["id"],
        category_id=category_id,
        period_start=period_start.isoformat(),
    )
    target = "общий лимит" if category_id is None else "лимит категории"
    await safe_edit(callback.message, f"Введите сумму ({target}), например 15000:")
    await callback.answer()


@router.message(LimitState.waiting_amount)
async def process_limit_amount(message: Message, state: FSMContext) -> None:
    amount = parse_amount(message.text)
    if amount is None:
        await message.answer("Введите сумму больше нуля, например 15000.")
        return

    data = await state.get_data()
    budget_id = data.get("budget_id")
    period_start = data.get("period_start")
    if not budget_id or not period_start:
        await state.clear()
        await message.answer("Сессия устарела, отправьте /start.")
        return

    await set_limit(budget_id, data.get("category_id"), period_start, amount)
    await state.clear()
    await message.answer(
        f"✅ Лимит сохранён: {format_money(amount)} ₽",
        reply_markup=back_to_menu_keyboard(),
    )


@router.callback_query(F.data.startswith("confirm_limits_"))
async def confirm_limits(callback: CallbackQuery) -> None:
    parts = callback.data.split("_")
    if len(parts) != 4:
        await callback.answer("Кнопка устарела", show_alert=True)
        return

    budget_id = int(parts[2])
    period_start = parts[3]
    user = await resolve_user(callback)
    if not await is_budget_member(budget_id, user["id"]):
        await callback.answer("Нет доступа", show_alert=True)
        return

    budget = await get_budget(budget_id)
    previous_start = get_previous_period_start(
        date.fromisoformat(period_start), budget["month_start_day"] or 10
    )
    copied = await copy_limits(budget_id, previous_start.isoformat(), period_start)
    await confirm_period(budget_id, period_start)

    if copied:
        text = f"✅ Скопировано лимитов из прошлого периода: {copied}."
    else:
        text = (
            "В прошлом периоде лимитов не было. "
            "Задайте их вручную в разделе «🎯 Лимиты»."
        )
    await safe_edit(callback.message, text, back_to_menu_keyboard())
    await callback.answer("Готово")


@router.callback_query(F.data.startswith("change_limits_"))
async def change_limits(callback: CallbackQuery) -> None:
    parts = callback.data.split("_")
    if len(parts) != 4:
        await callback.answer("Кнопка устарела", show_alert=True)
        return

    budget_id = int(parts[2])
    period_start = parts[3]
    user = await resolve_user(callback)
    if not await is_budget_member(budget_id, user["id"]):
        await callback.answer("Нет доступа", show_alert=True)
        return

    await confirm_period(budget_id, period_start)
    await set_active_budget(user["id"], budget_id)
    budget = await get_budget(budget_id)
    text, markup = await _limits_payload(budget)
    await safe_edit(callback.message, text, markup)
    await callback.answer()
