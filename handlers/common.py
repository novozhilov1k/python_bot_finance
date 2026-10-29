from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from access import resolve_budget
from database import (
    add_budget_member,
    count_budget_members,
    create_personal_budget,
    get_budget_by_invite,
    get_or_create_user,
    get_user_budgets,
    is_budget_member,
    set_active_budget,
)
from keyboards import main_menu_keyboard
from ui import safe_edit

router = Router()


async def main_menu_text(user) -> str:
    budget = await resolve_budget(user)
    name = budget["name"] if budget else "—"
    return (
        f"📊 Финансовый помощник\n"
        f"Бюджет: {name}\n\n"
        "💡 Просто отправьте сумму (например 450) — и выберите категорию.\n\n"
        "Или выберите действие:"
    )


async def _join_budget(message: Message, user, code: str) -> None:
    budget = await get_budget_by_invite(code)
    if not budget:
        await message.answer("Приглашение недействительно или устарело.")
        return
    if await is_budget_member(budget["id"], user["id"]):
        await set_active_budget(user["id"], budget["id"])
        await message.answer("Вы уже участник этого бюджета.")
        return

    budgets = await get_user_budgets(user["id"])
    if any(item["type"] == "shared" for item in budgets):
        await message.answer("Вы уже состоите в общем бюджете. Можно быть только в одном.")
        return
    if await count_budget_members(budget["id"]) >= 2:
        await message.answer("В этом бюджете уже двое участников.")
        return

    await add_budget_member(budget["id"], user["id"])
    await set_active_budget(user["id"], budget["id"])
    await message.answer(f"✅ Вы присоединились к бюджету «{budget['name']}».")


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    user = await get_or_create_user(message.from_user.id, message.from_user.full_name)

    parts = (message.text or "").split(maxsplit=1)
    payload = parts[1].strip() if len(parts) > 1 else ""
    if payload.startswith("inv_"):
        await _join_budget(message, user, payload[4:])
        user = await get_or_create_user(message.from_user.id, message.from_user.full_name)

    budgets = await get_user_budgets(user["id"])
    if not budgets:
        budget_id = await create_personal_budget(user["id"])
        await set_active_budget(user["id"], budget_id)
        user = await get_or_create_user(message.from_user.id, message.from_user.full_name)
    elif user["active_budget_id"] is None:
        await set_active_budget(user["id"], budgets[0]["id"])
        user = await get_or_create_user(message.from_user.id, message.from_user.full_name)

    await message.answer(await main_menu_text(user), reply_markup=main_menu_keyboard())


@router.callback_query(F.data == "main_menu")
async def back_to_main(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    user = await get_or_create_user(callback.from_user.id, callback.from_user.full_name)
    await safe_edit(callback.message, await main_menu_text(user), main_menu_keyboard())
    await callback.answer()
