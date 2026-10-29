"""Автоматика смены периода: отчёт за завершённый период и предложение лимитов."""

import logging
from datetime import date

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from database import (
    ensure_period,
    get_all_budgets,
    get_budget_members,
    get_expenses_for_period,
    set_current_period,
)
from keyboards import new_period_keyboard
from reports import build_period_stats
from services import format_period, get_period_end, get_period_start

logger = logging.getLogger(__name__)


async def _send(bot: Bot, chat_id: int, text: str, reply_markup=None) -> None:
    try:
        await bot.send_message(chat_id, text, reply_markup=reply_markup)
    except TelegramAPIError:
        logger.warning("Не удалось отправить сообщение в чат %s", chat_id, exc_info=True)


async def _send_closed_report(bot: Bot, budget, period_start: date) -> None:
    period_end = get_period_end(period_start)
    expenses = await get_expenses_for_period(
        budget["id"], period_start.isoformat(), period_end.isoformat()
    )
    if not expenses:
        return

    stats = await build_period_stats(budget["id"], period_start)
    text = f"🏁 Период {format_period(period_start)} завершён.\n\n{stats}"
    for member in await get_budget_members(budget["id"]):
        await _send(bot, member["telegram_id"], text)


async def _send_new_period_prompt(bot: Bot, budget, period_start: date) -> None:
    text = (
        f"🆕 Начался новый период: {format_period(period_start)}\n\n"
        "Применить лимиты прошлого периода?"
    )
    markup = new_period_keyboard(budget["id"], period_start.isoformat())
    for member in await get_budget_members(budget["id"]):
        await _send(bot, member["telegram_id"], text, markup)


async def run_period_checks(bot: Bot) -> None:
    """Вызывается периодически: реагирует на смену периода у каждого бюджета."""
    today = date.today()
    for budget in await get_all_budgets():
        current_start = get_period_start(today, budget["month_start_day"] or 10)
        stored = budget["current_period_start"]

        if not stored:
            await set_current_period(budget["id"], current_start.isoformat())
            await ensure_period(budget["id"], current_start.isoformat(), confirmed=1)
            continue

        stored_start = date.fromisoformat(stored)
        if current_start <= stored_start:
            continue

        await _send_closed_report(bot, budget, stored_start)
        await set_current_period(budget["id"], current_start.isoformat())
        await ensure_period(budget["id"], current_start.isoformat(), confirmed=0)
        await _send_new_period_prompt(bot, budget, current_start)
