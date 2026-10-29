import os
from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import AsyncIterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import asyncpg
from dotenv import load_dotenv

from services import get_period_start

load_dotenv()

DEFAULT_MONTH_START_DAY = 10


def _build_dsn(raw: str) -> str:
    """Убирает параметры, которые asyncpg не понимает (sslmode, channel_binding)."""
    parts = urlsplit(raw.strip())
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query)
        if key not in {"sslmode", "channel_binding"}
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


DATABASE_URL = _build_dsn(os.getenv("DATABASE_URL", ""))
POOL_MIN_SIZE = int(os.getenv("DB_POOL_MIN_SIZE", "1"))
POOL_MAX_SIZE = int(os.getenv("DB_POOL_MAX_SIZE", "5"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE NOT NULL,
    name TEXT,
    active_budget_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS budgets (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK(type IN ('personal', 'shared')),
    month_start_day INTEGER DEFAULT 10,
    owner_user_id INTEGER NOT NULL,
    current_period_start DATE,
    invite_code TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_budgets_invite_code ON budgets(invite_code);

CREATE TABLE IF NOT EXISTS budget_members (
    budget_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    role TEXT DEFAULT 'member',
    PRIMARY KEY (budget_id, user_id)
);

CREATE TABLE IF NOT EXISTS categories (
    id SERIAL PRIMARY KEY,
    budget_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    is_deleted INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS limits (
    id SERIAL PRIMARY KEY,
    budget_id INTEGER NOT NULL,
    category_id INTEGER,
    period_start DATE NOT NULL,
    amount DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(budget_id, category_id, period_start)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_general_limit
    ON limits(budget_id, period_start) WHERE category_id IS NULL;

CREATE TABLE IF NOT EXISTS expenses (
    id SERIAL PRIMARY KEY,
    budget_id INTEGER NOT NULL,
    category_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    amount DOUBLE PRECISION NOT NULL,
    spent_on DATE NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_expenses_budget_date ON expenses(budget_id, spent_on);
CREATE INDEX IF NOT EXISTS idx_expenses_category ON expenses(category_id);

CREATE TABLE IF NOT EXISTS periods (
    budget_id INTEGER NOT NULL,
    period_start DATE NOT NULL,
    limits_confirmed INTEGER DEFAULT 0,
    PRIMARY KEY (budget_id, period_start)
);
"""

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        if not DATABASE_URL:
            raise RuntimeError(
                "DATABASE_URL не задан. Укажите строку подключения к PostgreSQL "
                "в .env или в переменных окружения хостинга."
            )
        _pool = await asyncpg.create_pool(
            dsn=DATABASE_URL,
            min_size=POOL_MIN_SIZE,
            max_size=POOL_MAX_SIZE,
            ssl="require",
            command_timeout=30,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def connect() -> AsyncIterator[asyncpg.Connection]:
    pool = await get_pool()
    async with pool.acquire() as connection:
        yield connection


def _as_date(value) -> date:
    """Приводит ISO-строку или date к date (asyncpg требует настоящий тип)."""
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


async def init_db() -> None:
    pool = await get_pool()
    async with pool.acquire() as connection:
        await connection.execute(SCHEMA)

        # Миграции для баз, созданных более ранними версиями
        await connection.execute(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS active_budget_id INTEGER"
        )
        await connection.execute(
            "ALTER TABLE budgets ADD COLUMN IF NOT EXISTS current_period_start DATE"
        )
        await connection.execute(
            "ALTER TABLE budgets ADD COLUMN IF NOT EXISTS invite_code TEXT"
        )
        await connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_budgets_invite_code ON budgets(invite_code)"
        )

        stale = await connection.fetch(
            "SELECT id, month_start_day FROM budgets WHERE current_period_start IS NULL"
        )
        for row in stale:
            day = row["month_start_day"] or DEFAULT_MONTH_START_DAY
            period_start = get_period_start(date.today(), day)
            await connection.execute(
                "UPDATE budgets SET current_period_start = $1 WHERE id = $2",
                period_start,
                row["id"],
            )
            await connection.execute(
                "INSERT INTO periods (budget_id, period_start, limits_confirmed) "
                "VALUES ($1, $2, 1) ON CONFLICT DO NOTHING",
                row["id"],
                period_start,
            )


# --- Пользователи ---


async def get_or_create_user(telegram_id: int, name: str) -> asyncpg.Record:
    async with connect() as connection:
        return await connection.fetchrow(
            """
            INSERT INTO users (telegram_id, name) VALUES ($1, $2)
            ON CONFLICT (telegram_id) DO UPDATE SET name = EXCLUDED.name
            RETURNING *
            """,
            telegram_id,
            name,
        )


async def set_active_budget(user_id: int, budget_id: int) -> None:
    async with connect() as connection:
        await connection.execute(
            "UPDATE users SET active_budget_id = $1 WHERE id = $2", budget_id, user_id
        )


# --- Бюджеты ---


async def get_user_budgets(user_id: int) -> list[asyncpg.Record]:
    async with connect() as connection:
        return await connection.fetch(
            """
            SELECT b.* FROM budgets b
            JOIN budget_members bm ON bm.budget_id = b.id
            WHERE bm.user_id = $1
            ORDER BY b.id
            """,
            user_id,
        )


async def get_all_budgets() -> list[asyncpg.Record]:
    async with connect() as connection:
        return await connection.fetch("SELECT * FROM budgets ORDER BY id")


async def get_budget(budget_id: int) -> asyncpg.Record | None:
    async with connect() as connection:
        return await connection.fetchrow("SELECT * FROM budgets WHERE id = $1", budget_id)


async def is_budget_member(budget_id: int, user_id: int) -> bool:
    async with connect() as connection:
        return (
            await connection.fetchval(
                "SELECT 1 FROM budget_members WHERE budget_id = $1 AND user_id = $2",
                budget_id,
                user_id,
            )
            is not None
        )


async def get_budget_members(budget_id: int) -> list[asyncpg.Record]:
    async with connect() as connection:
        return await connection.fetch(
            """
            SELECT u.id, u.telegram_id, u.name, bm.role
            FROM budget_members bm
            JOIN users u ON u.id = bm.user_id
            WHERE bm.budget_id = $1
            ORDER BY bm.role DESC, u.id
            """,
            budget_id,
        )


async def count_budget_members(budget_id: int) -> int:
    async with connect() as connection:
        return int(
            await connection.fetchval(
                "SELECT COUNT(*) FROM budget_members WHERE budget_id = $1", budget_id
            )
        )


async def add_budget_member(budget_id: int, user_id: int, role: str = "member") -> None:
    async with connect() as connection:
        await connection.execute(
            "INSERT INTO budget_members (budget_id, user_id, role) VALUES ($1, $2, $3) "
            "ON CONFLICT DO NOTHING",
            budget_id,
            user_id,
            role,
        )


async def _create_budget(user_id: int, name: str, btype: str, month_start_day: int) -> int:
    period_start = get_period_start(date.today(), month_start_day)
    async with connect() as connection:
        async with connection.transaction():
            budget_id = await connection.fetchval(
                """
                INSERT INTO budgets
                    (name, type, month_start_day, owner_user_id, current_period_start)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id
                """,
                name,
                btype,
                month_start_day,
                user_id,
                period_start,
            )
            await connection.execute(
                "INSERT INTO budget_members (budget_id, user_id, role) "
                "VALUES ($1, $2, 'owner')",
                budget_id,
                user_id,
            )
            await connection.execute(
                "INSERT INTO periods (budget_id, period_start, limits_confirmed) "
                "VALUES ($1, $2, 1) ON CONFLICT DO NOTHING",
                budget_id,
                period_start,
            )
    return budget_id


async def create_personal_budget(user_id: int, name: str = "Личный") -> int:
    return await _create_budget(user_id, name, "personal", DEFAULT_MONTH_START_DAY)


async def create_shared_budget(user_id: int, name: str = "Общий") -> int:
    return await _create_budget(user_id, name, "shared", DEFAULT_MONTH_START_DAY)


async def set_invite_code(budget_id: int, code: str) -> None:
    async with connect() as connection:
        await connection.execute(
            "UPDATE budgets SET invite_code = $1 WHERE id = $2", code, budget_id
        )


async def get_budget_by_invite(code: str) -> asyncpg.Record | None:
    async with connect() as connection:
        return await connection.fetchrow("SELECT * FROM budgets WHERE invite_code = $1", code)


async def update_budget_start_day(
    budget_id: int, day: int, current_period_start: date | str
) -> None:
    async with connect() as connection:
        await connection.execute(
            "UPDATE budgets SET month_start_day = $1, current_period_start = $2 WHERE id = $3",
            day,
            _as_date(current_period_start),
            budget_id,
        )


# --- Категории ---


async def get_categories(budget_id: int) -> list[asyncpg.Record]:
    async with connect() as connection:
        return await connection.fetch(
            "SELECT * FROM categories WHERE budget_id = $1 AND is_deleted = 0 ORDER BY name",
            budget_id,
        )


async def get_category(category_id: int) -> asyncpg.Record | None:
    async with connect() as connection:
        return await connection.fetchrow("SELECT * FROM categories WHERE id = $1", category_id)


async def find_category_by_name(budget_id: int, name: str) -> asyncpg.Record | None:
    # Сравнение в Python: не зависим от настроек collation для кириллицы
    target = name.strip().casefold()
    for row in await get_categories(budget_id):
        if (row["name"] or "").casefold() == target:
            return row
    return None


async def create_category(budget_id: int, name: str) -> int:
    async with connect() as connection:
        return await connection.fetchval(
            "INSERT INTO categories (budget_id, name) VALUES ($1, $2) RETURNING id",
            budget_id,
            name,
        )


async def rename_category(category_id: int, name: str) -> None:
    async with connect() as connection:
        await connection.execute(
            "UPDATE categories SET name = $1 WHERE id = $2", name, category_id
        )


async def delete_category(category_id: int, transfer_to_category_id: int) -> None:
    async with connect() as connection:
        async with connection.transaction():
            await connection.execute(
                "UPDATE expenses SET category_id = $1, updated_at = CURRENT_TIMESTAMP "
                "WHERE category_id = $2",
                transfer_to_category_id,
                category_id,
            )
            # Лимит удалённой категории больше не имеет смысла
            await connection.execute("DELETE FROM limits WHERE category_id = $1", category_id)
            await connection.execute(
                "UPDATE categories SET is_deleted = 1 WHERE id = $1", category_id
            )


# --- Траты ---


async def add_expense(
    budget_id: int, category_id: int, user_id: int, amount: float, spent_on: date | str
) -> int:
    async with connect() as connection:
        return await connection.fetchval(
            """
            INSERT INTO expenses (budget_id, category_id, user_id, amount, spent_on)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id
            """,
            budget_id,
            category_id,
            user_id,
            float(amount),
            _as_date(spent_on),
        )


async def get_expenses_by_category(
    budget_id: int, category_id: int, start_date: date | str, end_date: date | str
) -> list[asyncpg.Record]:
    async with connect() as connection:
        return await connection.fetch(
            """
            SELECT * FROM expenses
            WHERE budget_id = $1 AND category_id = $2 AND spent_on BETWEEN $3 AND $4
            ORDER BY spent_on DESC, id DESC
            """,
            budget_id,
            category_id,
            _as_date(start_date),
            _as_date(end_date),
        )


async def get_expenses_for_period(
    budget_id: int, start_date: date | str, end_date: date | str
) -> list[asyncpg.Record]:
    async with connect() as connection:
        return await connection.fetch(
            """
            SELECT e.*, c.name AS category_name
            FROM expenses e
            JOIN categories c ON c.id = e.category_id
            WHERE e.budget_id = $1 AND e.spent_on BETWEEN $2 AND $3
            ORDER BY e.spent_on DESC, e.id DESC
            """,
            budget_id,
            _as_date(start_date),
            _as_date(end_date),
        )


async def get_expense_sums(
    budget_id: int, start_date: date | str, end_date: date | str
) -> dict[int, float]:
    async with connect() as connection:
        rows = await connection.fetch(
            """
            SELECT category_id, SUM(amount) AS total
            FROM expenses
            WHERE budget_id = $1 AND spent_on BETWEEN $2 AND $3
            GROUP BY category_id
            """,
            budget_id,
            _as_date(start_date),
            _as_date(end_date),
        )
    return {row["category_id"]: float(row["total"]) for row in rows}


async def get_expense(expense_id: int) -> asyncpg.Record | None:
    async with connect() as connection:
        return await connection.fetchrow("SELECT * FROM expenses WHERE id = $1", expense_id)


async def delete_expense(expense_id: int) -> None:
    async with connect() as connection:
        await connection.execute("DELETE FROM expenses WHERE id = $1", expense_id)


async def update_expense(expense_id: int, field: str, value) -> None:
    if field not in {"amount", "category_id"}:
        raise ValueError(f"Недопустимое поле: {field}")
    if field == "amount":
        value = float(value)
    async with connect() as connection:
        await connection.execute(
            f"UPDATE expenses SET {field} = $1, updated_at = CURRENT_TIMESTAMP WHERE id = $2",
            value,
            expense_id,
        )


# --- Лимиты ---


async def get_limits_for_period(
    budget_id: int, period_start: date | str
) -> list[asyncpg.Record]:
    async with connect() as connection:
        return await connection.fetch(
            """
            SELECT * FROM limits
            WHERE budget_id = $1 AND period_start = $2
            ORDER BY (category_id IS NULL) DESC, category_id
            """,
            budget_id,
            _as_date(period_start),
        )


async def get_limit(
    budget_id: int, category_id: int | None, period_start: date | str
) -> asyncpg.Record | None:
    async with connect() as connection:
        if category_id is None:
            return await connection.fetchrow(
                "SELECT * FROM limits WHERE budget_id = $1 AND category_id IS NULL "
                "AND period_start = $2",
                budget_id,
                _as_date(period_start),
            )
        return await connection.fetchrow(
            "SELECT * FROM limits WHERE budget_id = $1 AND category_id = $2 "
            "AND period_start = $3",
            budget_id,
            category_id,
            _as_date(period_start),
        )


async def set_limit(
    budget_id: int, category_id: int | None, period_start: date | str, amount: float
) -> None:
    period = _as_date(period_start)
    async with connect() as connection:
        async with connection.transaction():
            if category_id is None:
                existing_id = await connection.fetchval(
                    "SELECT id FROM limits WHERE budget_id = $1 AND category_id IS NULL "
                    "AND period_start = $2",
                    budget_id,
                    period,
                )
            else:
                existing_id = await connection.fetchval(
                    "SELECT id FROM limits WHERE budget_id = $1 AND category_id = $2 "
                    "AND period_start = $3",
                    budget_id,
                    category_id,
                    period,
                )

            if existing_id:
                await connection.execute(
                    "UPDATE limits SET amount = $1, updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = $2",
                    float(amount),
                    existing_id,
                )
            else:
                await connection.execute(
                    "INSERT INTO limits (budget_id, category_id, period_start, amount) "
                    "VALUES ($1, $2, $3, $4)",
                    budget_id,
                    category_id,
                    period,
                    float(amount),
                )

            # Пользователь задал лимиты — подтверждение периода больше не нужно
            await connection.execute(
                "INSERT INTO periods (budget_id, period_start, limits_confirmed) "
                "VALUES ($1, $2, 1) ON CONFLICT (budget_id, period_start) "
                "DO UPDATE SET limits_confirmed = 1",
                budget_id,
                period,
            )


async def copy_limits(
    budget_id: int, from_period: date | str, to_period: date | str
) -> int:
    source = _as_date(from_period)
    target = _as_date(to_period)
    async with connect() as connection:
        async with connection.transaction():
            rows = await connection.fetch(
                "SELECT category_id, amount FROM limits "
                "WHERE budget_id = $1 AND period_start = $2",
                budget_id,
                source,
            )
            for row in rows:
                await connection.execute(
                    "INSERT INTO limits (budget_id, category_id, period_start, amount) "
                    "VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING",
                    budget_id,
                    row["category_id"],
                    target,
                    row["amount"],
                )
            await connection.execute(
                "INSERT INTO periods (budget_id, period_start, limits_confirmed) "
                "VALUES ($1, $2, 1) ON CONFLICT (budget_id, period_start) "
                "DO UPDATE SET limits_confirmed = 1",
                budget_id,
                target,
            )
    return len(rows)


# --- Периоды ---


async def ensure_period(budget_id: int, period_start: date | str, confirmed: int = 0) -> None:
    async with connect() as connection:
        await connection.execute(
            "INSERT INTO periods (budget_id, period_start, limits_confirmed) "
            "VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
            budget_id,
            _as_date(period_start),
            confirmed,
        )


async def get_period_row(budget_id: int, period_start: date | str) -> asyncpg.Record | None:
    async with connect() as connection:
        return await connection.fetchrow(
            "SELECT * FROM periods WHERE budget_id = $1 AND period_start = $2",
            budget_id,
            _as_date(period_start),
        )


async def confirm_period(budget_id: int, period_start: date | str) -> None:
    async with connect() as connection:
        await connection.execute(
            "INSERT INTO periods (budget_id, period_start, limits_confirmed) "
            "VALUES ($1, $2, 1) ON CONFLICT (budget_id, period_start) "
            "DO UPDATE SET limits_confirmed = 1",
            budget_id,
            _as_date(period_start),
        )


async def set_current_period(budget_id: int, period_start: date | str) -> None:
    async with connect() as connection:
        await connection.execute(
            "UPDATE budgets SET current_period_start = $1 WHERE id = $2",
            _as_date(period_start),
            budget_id,
        )


# --- Обслуживание ---


async def cleanup_old_data(months: int = 6) -> None:
    cutoff = date.today() - timedelta(days=months * 30)
    async with connect() as connection:
        async with connection.transaction():
            await connection.execute("DELETE FROM expenses WHERE spent_on < $1", cutoff)
            await connection.execute("DELETE FROM limits WHERE period_start < $1", cutoff)
            await connection.execute("DELETE FROM periods WHERE period_start < $1", cutoff)
