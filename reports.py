"""Сборка текстовых отчётов по бюджету за период."""

from datetime import date

from database import get_categories, get_expense_sums, get_limits_for_period
from services import (
    format_money,
    format_period,
    format_progress,
    get_period_end,
    progress_status,
)


def _category_block(name: str, spent: float, limit: float | None) -> list[str]:
    """Одна категория: строка с суммой и, при наличии лимита, прогресс-бар."""
    if not limit:
        return [f"• {name}: {format_money(spent)} ₽"]

    status = progress_status(spent, limit)
    bar = format_progress(spent, limit)
    lines = [f"{status} {name}: {format_money(spent)} / {format_money(limit)} ₽"]
    if spent > limit:
        lines.append(f"   {bar} · перерасход {format_money(spent - limit)} ₽")
    else:
        lines.append(f"   {bar} · осталось {format_money(limit - spent)} ₽")
    return lines


async def build_period_stats(budget_id: int, period_start: date) -> str:
    period_end = get_period_end(period_start)
    categories = await get_categories(budget_id)
    limits = await get_limits_for_period(budget_id, period_start.isoformat())
    sums = await get_expense_sums(budget_id, period_start.isoformat(), period_end.isoformat())

    limit_map = {
        row["category_id"]: float(row["amount"])
        for row in limits
        if row["category_id"] is not None
    }
    general_limit = next(
        (float(row["amount"]) for row in limits if row["category_id"] is None), None
    )

    lines = [f"📈 Статистика за период: {format_period(period_start)}", ""]
    if not categories:
        lines.append("Категорий пока нет.")

    total = 0.0
    blocks: list[str] = []
    for category in categories:
        spent = sums.get(category["id"], 0.0)
        total += spent
        blocks.append("\n".join(_category_block(category["name"], spent, limit_map.get(category["id"]))))

    if blocks:
        lines.extend(blocks)

    lines.append("")
    if general_limit:
        left = general_limit - total
        lines.append(f"📊 Всего: {format_money(total)} / {format_money(general_limit)} ₽")
        lines.append(format_progress(total, general_limit))
        if left >= 0:
            lines.append(f"Осталось: {format_money(left)} ₽")
        else:
            lines.append(f"⚠️ Перерасход: {format_money(-left)} ₽")
    else:
        lines.append(f"📊 Всего: {format_money(total)} ₽")

    return "\n".join(lines)
