import secrets
from datetime import date

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from access import resolve_budget, resolve_user
from database import (
    create_shared_budget,
    ensure_period,
    get_or_create_user,
    get_user_budgets,
    is_budget_member,
    set_active_budget,
    set_invite_code,
    update_budget_start_day,
)
from handlers.common import main_menu_text
from keyboards import back_to_menu_keyboard, budgets_keyboard, main_menu_keyboard
from services import (
    can_change_start_day,
    format_date,
    get_period_end,
    get_period_start,
)
from states import BudgetState
from ui import safe_edit

router = Router()


@router.callback_query(F.data == "switch_budget")
async def switch_budget(callback: CallbackQuery) -> None:
    user = await resolve_user(callback)
    budgets = await get_user_budgets(user["id"])
    active = await resolve_budget(user)
    shared = next((item for item in budgets if item["type"] == "shared"), None)

    lines = ["🔄 Ваши бюджеты:", ""]
    for item in budgets:
        kind = "личный" if item["type"] == "personal" else "общий"
        lines.append(f"• {item['name']} ({kind})")
    if shared is None:
        lines.append("")
        lines.append("Общего бюджета пока нет — его можно создать.")

    await safe_edit(
        callback.message,
        "\n".join(lines),
        budgets_keyboard(
            budgets,
            active["id"] if active else None,
            can_create_shared=shared is None,
            can_invite=bool(shared and shared["owner_user_id"] == user["id"]),
            can_settings=bool(active and active["owner_user_id"] == user["id"]),
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("set_budget_"))
async def set_budget(callback: CallbackQuery, state: FSMContext) -> None:
    budget_id = int(callback.data.removeprefix("set_budget_"))
    user = await resolve_user(callback)
    if not await is_budget_member(budget_id, user["id"]):
        await callback.answer("Нет доступа к этому бюджету", show_alert=True)
        return

    await state.clear()
    await set_active_budget(user["id"], budget_id)
    user = await get_or_create_user(callback.from_user.id, callback.from_user.full_name)
    await safe_edit(callback.message, await main_menu_text(user), main_menu_keyboard())
    await callback.answer("Бюджет переключён")


@router.callback_query(F.data == "create_shared_budget")
async def create_shared(callback: CallbackQuery, state: FSMContext) -> None:
    user = await resolve_user(callback)
    budgets = await get_user_budgets(user["id"])
    if any(item["type"] == "shared" for item in budgets):
        await callback.answer("Общий бюджет уже есть", show_alert=True)
        return

    budget_id = await create_shared_budget(user["id"])
    await state.clear()
    await set_active_budget(user["id"], budget_id)
    await safe_edit(
        callback.message,
        "✅ Общий бюджет создан.\n\n"
        "Откройте «🔄 Бюджеты» → «👥 Пригласить участника» и отправьте ссылку второму человеку.",
        back_to_menu_keyboard(),
    )
    await callback.answer("Готово")


@router.callback_query(F.data == "invite_member")
async def invite_member(callback: CallbackQuery) -> None:
    user = await resolve_user(callback)
    budget = await resolve_budget(user)
    if not budget or budget["type"] != "shared" or budget["owner_user_id"] != user["id"]:
        await callback.answer("Приглашать может только владелец общего бюджета", show_alert=True)
        return

    code = budget["invite_code"]
    if not code:
        code = secrets.token_hex(4)
        await set_invite_code(budget["id"], code)

    me = await callback.bot.get_me()
    link = f"https://t.me/{me.username}?start=inv_{code}"
    await safe_edit(
        callback.message,
        f"👥 Приглашение в бюджет «{budget['name']}»:\n\n{link}\n\n"
        "Отправьте эту ссылку второму участнику. Она бессрочная.",
        back_to_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "change_start_day")
async def change_start_day(callback: CallbackQuery, state: FSMContext) -> None:
    user = await resolve_user(callback)
    budget = await resolve_budget(user)
    if not budget or budget["owner_user_id"] != user["id"]:
        await callback.answer("Менять настройку может только владелец", show_alert=True)
        return

    today = date.today()
    period_start = get_period_start(today, budget["month_start_day"])
    if not can_change_start_day(today, period_start):
        allowed = (
            f"{format_date(period_start.isoformat())} или "
            f"{format_date(get_period_end(period_start).isoformat())}"
        )
        await safe_edit(
            callback.message,
            f"День начала периода можно менять только {allowed}.\n"
            f"Сейчас у вас {budget['month_start_day']}-е число.",
            back_to_menu_keyboard(),
        )
        await callback.answer()
        return

    await state.set_state(BudgetState.waiting_start_day)
    await safe_edit(
        callback.message,
        f"Сейчас период начинается {budget['month_start_day']}-го числа.\n"
        "Введите новый день (целое число от 1 до 28):",
    )
    await callback.answer()


@router.message(BudgetState.waiting_start_day)
async def process_start_day(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text.isdigit() or not 1 <= int(text) <= 28:
        await message.answer("Введите целое число от 1 до 28.")
        return

    user = await get_or_create_user(message.from_user.id, message.from_user.full_name)
    budget = await resolve_budget(user)
    if not budget or budget["owner_user_id"] != user["id"]:
        await state.clear()
        await message.answer("Нет доступа.", reply_markup=back_to_menu_keyboard())
        return

    today = date.today()
    period_start = get_period_start(today, budget["month_start_day"])
    if not can_change_start_day(today, period_start):
        await state.clear()
        await message.answer(
            "Сейчас менять день начала периода нельзя.", reply_markup=back_to_menu_keyboard()
        )
        return

    new_day = int(text)
    new_start = get_period_start(today, new_day)
    await update_budget_start_day(budget["id"], new_day, new_start.isoformat())
    await ensure_period(budget["id"], new_start.isoformat(), confirmed=1)
    await state.clear()
    await message.answer(
        f"✅ День начала периода изменён на {new_day}-е число.",
        reply_markup=back_to_menu_keyboard(),
    )
