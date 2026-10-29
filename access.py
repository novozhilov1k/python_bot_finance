"""Определение пользователя и его активного бюджета по событию Telegram."""

from database import (
    get_budget,
    get_or_create_user,
    get_user_budgets,
    is_budget_member,
    set_active_budget,
)


async def resolve_user(event) -> object:
    """Возвращает строку users по telegram_id, создавая пользователя при необходимости."""
    from_user = event.from_user
    return await get_or_create_user(from_user.id, from_user.full_name)


async def resolve_budget(user):
    """Активный бюджет пользователя. Если он не задан или недоступен — первый доступный."""
    budget_id = user["active_budget_id"]
    if budget_id and await is_budget_member(budget_id, user["id"]):
        return await get_budget(budget_id)

    budgets = await get_user_budgets(user["id"])
    if not budgets:
        return None
    await set_active_budget(user["id"], budgets[0]["id"])
    return await get_budget(budgets[0]["id"])
