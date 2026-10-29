"""Расчёт периодов, форматирование денег и дат. Без зависимостей от БД."""

import calendar
import math
from datetime import date, timedelta


def _clamp_day(year: int, month: int, day: int) -> int:
    """Не даёт дню выйти за пределы месяца (например, 31 февраля)."""
    return min(day, calendar.monthrange(year, month)[1])


def get_period_start(today: date, month_start_day: int = 10) -> date:
    """Начало текущего периода. Если день ещё не наступил — предыдущий месяц."""
    day = max(1, min(int(month_start_day), 31))
    start_day = _clamp_day(today.year, today.month, day)
    if today.day >= start_day:
        return date(today.year, today.month, start_day)
    year = today.year if today.month > 1 else today.year - 1
    month = today.month - 1 or 12
    return date(year, month, _clamp_day(year, month, day))


def get_period_end(period_start: date) -> date:
    """Последний день периода — день перед следующим началом."""
    year = period_start.year + (1 if period_start.month == 12 else 0)
    month = 1 if period_start.month == 12 else period_start.month + 1
    next_start = date(year, month, _clamp_day(year, month, period_start.day))
    return next_start - timedelta(days=1)


def get_previous_period_start(period_start: date, month_start_day: int) -> date:
    return get_period_start(period_start - timedelta(days=1), month_start_day)


def can_change_start_day(today: date, period_start: date) -> bool:
    """Менять день начала можно только в первый или последний день периода."""
    return today == period_start or today == get_period_end(period_start)


def format_period(period_start: date) -> str:
    return f"{period_start.strftime('%d.%m')} – {get_period_end(period_start).strftime('%d.%m')}"


def format_date(value) -> str:
    try:
        return date.fromisoformat(str(value)).strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        return str(value)


def format_money(value: float) -> str:
    rounded = round(float(value), 2)
    text = f"{rounded:.2f}".rstrip("0").rstrip(".")
    whole, _, frac = text.partition(".")
    whole = f"{int(whole):,}".replace(",", " ")
    return f"{whole},{frac}" if frac else whole


def parse_amount(text: str | None) -> float | None:
    """Разбирает '1 200,50' / '1200.50'. Возвращает None при ошибке."""
    if not text:
        return None
    cleaned = text.strip().replace("\u00a0", "").replace(" ", "").replace(",", ".")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if not math.isfinite(value) or value <= 0 or value > 10_000_000:
        return None
    return round(value, 2)
