import os
from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import AsyncIterator

import aiosqlite
from dotenv import load_dotenv

from services import get_period_start

load_dotenv()

DB_PATH = os.getenv("DATABASE_PATH", "finance.db")
DEFAULT_MONTH_START_DAY = 10

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    telegram_id INTEGER UNIQUE NOT NULL,
    name TEXT,
    active_budget_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS budgets (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK(type IN ('personal', 'shared')),
    month_start_day INTEGER DEFAULT 10,
    owner_user_id INTEGER NOT NULL,
    current_period_start DATE,
    invite_code TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS budget_members (
    budget_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    role TEXT DEFAULT 'member',
    PRIMARY KEY (budget_id, user_id)
);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY,
    budget_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    is_deleted INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS limits (
    id INTEGER PRIMARY KEY,
    budget_id INTEGER NOT NULL,
    category_id INTEGER,
    period_start DATE NOT NULL,
    amount REAL NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(budget_id, category_id, period_start)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_general_limit
    ON limits(budget_id, period_start) WHERE category_id IS NULL;

CREATE TABLE IF NOT EXISTS expenses (
    id INTEGER PRIMARY KEY,
    budget_id INTEGER NOT NULL,
    category_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    amount REAL NOT NULL,
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


@asynccontextmanager
async def connect() -> AsyncIterator[aiosqlite.Connection]:
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL;")
    await db.execute("PRAGMA busy_timeout=5000;")
    try:
        yield db
    finally:
        await db.close()


async def _add_column(db: aiosqlite.Connection, table: str, column: str, definition: str) -> None:
    async with db.execute(f"PRAGMA table_info({table})") as cur:
        columns = {row["name"] for row in await cur.fetchall()}
    if column not in columns:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


async def init_db() -> None:
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA busy_timeout=5000;")
        await db.executescript(SCHEMA)

        # Миграции для баз, созданных предыдущей версией
        await _add_column(db, "users", "active_budget_id", "active_budget_id INTEGER")
        await _add_column(db, "budgets", "current_period_start", "current_period_start DATE")
        await _add_column(db, "budgets", "invite_code", "invite_code TEXT")
        await db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_budgets_invite_code ON budgets(invite_code)"
        )

        async with db.execute(
            "SELECT id, month_start_day FROM budgets WHERE current_period_start IS NULL"
        ) as cur:
            stale = await cur.fetchall()
        for row in stale:
            day = row["month_start_day"] or DEFAULT_MONTH_START_DAY
            period_start = get_period_start(date.today(), day).isoformat()
            await db.execute(
                "UPDATE budgets SET current_period_start = ? WHERE id = ?",
                (period_start, row["id"]),
            )
            await db.execute(
                "INSERT OR IGNORE INTO periods (budget_id, period_start, limits_confirmed) "
                "VALUES (?, ?, 1)",
                (row["id"], period_start),
            )

        await db.commit()


# --- Пользователи ---


async def get_or_create_user(telegram_id: int, name: str) -> aiosqlite.Row:
    async with connect() as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (telegram_id, name) VALUES (?, ?)",
            (telegram_id, name),
        )
        await db.execute(
            "UPDATE users SET name = ? WHERE telegram_id = ? AND (name IS NULL OR name <> ?)",
            (name, telegram_id, name),
        )
        await db.commit()
        async with db.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)) as cur:
            return await cur.fetchone()


async def set_active_budget(user_id: int, budget_id: int) -> None:
    async with connect() as db:
        await db.execute("UPDATE users SET active_budget_id = ? WHERE id = ?", (budget_id, user_id))
        await db.commit()


# --- Бюджеты ---


async def get_user_budgets(user_id: int) -> list[aiosqlite.Row]:
    async with connect() as db:
        async with db.execute(
            """
            SELECT b.* FROM budgets b
            JOIN budget_members bm ON bm.budget_id = b.id
            WHERE bm.user_id = ?
            ORDER BY b.id
            """,
            (user_id,),
        ) as cur:
            return await cur.fetchall()


async def get_all_budgets() -> list[aiosqlite.Row]:
    async with connect() as db:
        async with db.execute("SELECT * FROM budgets ORDER BY id") as cur:
            return await cur.fetchall()


async def get_budget(budget_id: int) -> aiosqlite.Row | None:
    async with connect() as db:
        async with db.execute("SELECT * FROM budgets WHERE id = ?", (budget_id,)) as cur:
            return await cur.fetchone()


async def is_budget_member(budget_id: int, user_id: int) -> bool:
    async with connect() as db:
        async with db.execute(
            "SELECT 1 FROM budget_members WHERE budget_id = ? AND user_id = ?",
            (budget_id, user_id),
        ) as cur:
            return await cur.fetchone() is not None


async def get_budget_members(budget_id: int) -> list[aiosqlite.Row]:
    async with connect() as db:
        async with db.execute(
            """
            SELECT u.id, u.telegram_id, u.name, bm.role
            FROM budget_members bm
            JOIN users u ON u.id = bm.user_id
            WHERE bm.budget_id = ?
            ORDER BY bm.role DESC, u.id
            """,
            (budget_id,),
        ) as cur:
            return await cur.fetchall()


async def count_budget_members(budget_id: int) -> int:
    async with connect() as db:
        async with db.execute(
            "SELECT COUNT(*) AS cnt FROM budget_members WHERE budget_id = ?", (budget_id,)
        ) as cur:
            row = await cur.fetchone()
            return int(row["cnt"])


async def add_budget_member(budget_id: int, user_id: int, role: str = "member") -> None:
    async with connect() as db:
        await db.execute(
            "INSERT OR IGNORE INTO budget_members (budget_id, user_id, role) VALUES (?, ?, ?)",
            (budget_id, user_id, role),
        )
        await db.commit()


async def _create_budget(user_id: int, name: str, btype: str, month_start_day: int) -> int:
    period_start = get_period_start(date.today(), month_start_day).isoformat()
    async with connect() as db:
        cur = await db.execute(
            """
            INSERT INTO budgets (name, type, month_start_day, owner_user_id, current_period_start)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, btype, month_start_day, user_id, period_start),
        )
        budget_id = cur.lastrowid
        await db.execute(
            "INSERT INTO budget_members (budget_id, user_id, role) VALUES (?, ?, 'owner')",
            (budget_id, user_id),
        )
        await db.execute(
            "INSERT OR IGNORE INTO periods (budget_id, period_start, limits_confirmed) "
            "VALUES (?, ?, 1)",
            (budget_id, period_start),
        )
        await db.commit()
    return budget_id


async def create_personal_budget(user_id: int, name: str = "Личный") -> int:
    return await _create_budget(user_id, name, "personal", DEFAULT_MONTH_START_DAY)


async def create_shared_budget(user_id: int, name: str = "Общий") -> int:
    return await _create_budget(user_id, name, "shared", DEFAULT_MONTH_START_DAY)


async def set_invite_code(budget_id: int, code: str) -> None:
    async with connect() as db:
        await db.execute("UPDATE budgets SET invite_code = ? WHERE id = ?", (code, budget_id))
        await db.commit()


async def get_budget_by_invite(code: str) -> aiosqlite.Row | None:
    async with connect() as db:
        async with db.execute("SELECT * FROM budgets WHERE invite_code = ?", (code,)) as cur:
            return await cur.fetchone()


async def update_budget_start_day(budget_id: int, day: int, current_period_start: str) -> None:
    async with connect() as db:
        await db.execute(
            "UPDATE budgets SET month_start_day = ?, current_period_start = ? WHERE id = ?",
            (day, current_period_start, budget_id),
        )
        await db.commit()


# --- Категории ---


async def get_categories(budget_id: int) -> list[aiosqlite.Row]:
    async with connect() as db:
        async with db.execute(
            "SELECT * FROM categories WHERE budget_id = ? AND is_deleted = 0 ORDER BY name",
            (budget_id,),
        ) as cur:
            return await cur.fetchall()


async def get_category(category_id: int) -> aiosqlite.Row | None:
    async with connect() as db:
        async with db.execute("SELECT * FROM categories WHERE id = ?", (category_id,)) as cur:
            return await cur.fetchone()


async def find_category_by_name(budget_id: int, name: str) -> aiosqlite.Row | None:
    # LOWER() в SQLite не работает с кириллицей, поэтому сравниваем в Python
    target = name.strip().casefold()
    for row in await get_categories(budget_id):
        if (row["name"] or "").casefold() == target:
            return row
    return None


async def create_category(budget_id: int, name: str) -> int:
    async with connect() as db:
        cur = await db.execute(
            "INSERT INTO categories (budget_id, name) VALUES (?, ?)", (budget_id, name)
        )
        await db.commit()
        return cur.lastrowid


async def rename_category(category_id: int, name: str) -> None:
    async with connect() as db:
        await db.execute("UPDATE categories SET name = ? WHERE id = ?", (name, category_id))
        await db.commit()


async def delete_category(category_id: int, transfer_to_category_id: int) -> None:
    async with connect() as db:
        await db.execute(
            "UPDATE expenses SET category_id = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE category_id = ?",
            (transfer_to_category_id, category_id),
        )
        # Лимит удалённой категории больше не имеет смысла
        await db.execute("DELETE FROM limits WHERE category_id = ?", (category_id,))
        await db.execute("UPDATE categories SET is_deleted = 1 WHERE id = ?", (category_id,))
        await db.commit()


# --- Траты ---


async def add_expense(
    budget_id: int, category_id: int, user_id: int, amount: float, spent_on: str
) -> int:
    async with connect() as db:
        cur = await db.execute(
            """
            INSERT INTO expenses (budget_id, category_id, user_id, amount, spent_on)
            VALUES (?, ?, ?, ?, ?)
            """,
            (budget_id, category_id, user_id, amount, spent_on),
        )
        await db.commit()
        return cur.lastrowid


async def get_expenses_by_category(
    budget_id: int, category_id: int, start_date: str, end_date: str
) -> list[aiosqlite.Row]:
    async with connect() as db:
        async with db.execute(
            """
            SELECT * FROM expenses
            WHERE budget_id = ? AND category_id = ? AND spent_on BETWEEN ? AND ?
            ORDER BY spent_on DESC, id DESC
            """,
            (budget_id, category_id, start_date, end_date),
        ) as cur:
            return await cur.fetchall()


async def get_expenses_for_period(
    budget_id: int, start_date: str, end_date: str
) -> list[aiosqlite.Row]:
    async with connect() as db:
        async with db.execute(
            """
            SELECT e.*, c.name AS category_name
            FROM expenses e
            JOIN categories c ON c.id = e.category_id
            WHERE e.budget_id = ? AND e.spent_on BETWEEN ? AND ?
            ORDER BY e.spent_on DESC, e.id DESC
            """,
            (budget_id, start_date, end_date),
        ) as cur:
            return await cur.fetchall()


async def get_expense_sums(budget_id: int, start_date: str, end_date: str) -> dict[int, float]:
    async with connect() as db:
        async with db.execute(
            """
            SELECT category_id, SUM(amount) AS total
            FROM expenses
            WHERE budget_id = ? AND spent_on BETWEEN ? AND ?
            GROUP BY category_id
            """,
            (budget_id, start_date, end_date),
        ) as cur:
            rows = await cur.fetchall()
    return {row["category_id"]: float(row["total"]) for row in rows}


async def get_expense(expense_id: int) -> aiosqlite.Row | None:
    async with connect() as db:
        async with db.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)) as cur:
            return await cur.fetchone()


async def delete_expense(expense_id: int) -> None:
    async with connect() as db:
        await db.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
        await db.commit()


async def update_expense(expense_id: int, field: str, value) -> None:
    if field not in {"amount", "category_id"}:
        raise ValueError(f"Недопустимое поле: {field}")
    async with connect() as db:
        await db.execute(
            f"UPDATE expenses SET {field} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (value, expense_id),
        )
        await db.commit()


# --- Лимиты ---


async def get_limits_for_period(budget_id: int, period_start: str) -> list[aiosqlite.Row]:
    async with connect() as db:
        async with db.execute(
            """
            SELECT * FROM limits
            WHERE budget_id = ? AND period_start = ?
            ORDER BY (category_id IS NULL) DESC, category_id
            """,
            (budget_id, period_start),
        ) as cur:
            return await cur.fetchall()


async def get_limit(
    budget_id: int, category_id: int | None, period_start: str
) -> aiosqlite.Row | None:
    async with connect() as db:
        if category_id is None:
            query = (
                "SELECT * FROM limits WHERE budget_id = ? AND category_id IS NULL "
                "AND period_start = ?"
            )
            params = (budget_id, period_start)
        else:
            query = (
                "SELECT * FROM limits WHERE budget_id = ? AND category_id = ? "
                "AND period_start = ?"
            )
            params = (budget_id, category_id, period_start)
        async with db.execute(query, params) as cur:
            return await cur.fetchone()


async def set_limit(
    budget_id: int, category_id: int | None, period_start: str, amount: float
) -> None:
    async with connect() as db:
        if category_id is None:
            query = (
                "SELECT * FROM limits WHERE budget_id = ? AND category_id IS NULL "
                "AND period_start = ?"
            )
            params = (budget_id, period_start)
        else:
            query = (
                "SELECT * FROM limits WHERE budget_id = ? AND category_id = ? "
                "AND period_start = ?"
            )
            params = (budget_id, category_id, period_start)
        async with db.execute(query, params) as cur:
            existing = await cur.fetchone()
        if existing:
            await db.execute(
                "UPDATE limits SET amount = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (amount, existing["id"]),
            )
        else:
            await db.execute(
                """
                INSERT INTO limits (budget_id, category_id, period_start, amount)
                VALUES (?, ?, ?, ?)
                """,
                (budget_id, category_id, period_start, amount),
            )
        # Пользователь задал лимиты — подтверждение периода больше не нужно
        await db.execute(
            "INSERT OR IGNORE INTO periods (budget_id, period_start, limits_confirmed) "
            "VALUES (?, ?, 1)",
            (budget_id, period_start),
        )
        await db.execute(
            "UPDATE periods SET limits_confirmed = 1 WHERE budget_id = ? AND period_start = ?",
            (budget_id, period_start),
        )
        await db.commit()


async def copy_limits(budget_id: int, from_period: str, to_period: str) -> int:
    async with connect() as db:
        async with db.execute(
            "SELECT category_id, amount FROM limits WHERE budget_id = ? AND period_start = ?",
            (budget_id, from_period),
        ) as cur:
            rows = await cur.fetchall()
        for row in rows:
            await db.execute(
                """
                INSERT OR IGNORE INTO limits (budget_id, category_id, period_start, amount)
                VALUES (?, ?, ?, ?)
                """,
                (budget_id, row["category_id"], to_period, row["amount"]),
            )
        await db.execute(
            "INSERT OR IGNORE INTO periods (budget_id, period_start, limits_confirmed) "
            "VALUES (?, ?, 1)",
            (budget_id, to_period),
        )
        await db.execute(
            "UPDATE periods SET limits_confirmed = 1 WHERE budget_id = ? AND period_start = ?",
            (budget_id, to_period),
        )
        await db.commit()
        return len(rows)


# --- Периоды ---


async def ensure_period(budget_id: int, period_start: str, confirmed: int = 0) -> None:
    async with connect() as db:
        await db.execute(
            "INSERT OR IGNORE INTO periods (budget_id, period_start, limits_confirmed) "
            "VALUES (?, ?, ?)",
            (budget_id, period_start, confirmed),
        )
        await db.commit()


async def get_period_row(budget_id: int, period_start: str) -> aiosqlite.Row | None:
    async with connect() as db:
        async with db.execute(
            "SELECT * FROM periods WHERE budget_id = ? AND period_start = ?",
            (budget_id, period_start),
        ) as cur:
            return await cur.fetchone()


async def confirm_period(budget_id: int, period_start: str) -> None:
    async with connect() as db:
        await db.execute(
            "INSERT OR IGNORE INTO periods (budget_id, period_start, limits_confirmed) "
            "VALUES (?, ?, 1)",
            (budget_id, period_start),
        )
        await db.execute(
            "UPDATE periods SET limits_confirmed = 1 WHERE budget_id = ? AND period_start = ?",
            (budget_id, period_start),
        )
        await db.commit()


async def set_current_period(budget_id: int, period_start: str) -> None:
    async with connect() as db:
        await db.execute(
            "UPDATE budgets SET current_period_start = ? WHERE id = ?",
            (period_start, budget_id),
        )
        await db.commit()


# --- Обслуживание ---


async def cleanup_old_data(months: int = 6) -> None:
    cutoff = (date.today() - timedelta(days=months * 30)).isoformat()
    async with connect() as db:
        await db.execute("DELETE FROM expenses WHERE spent_on < ?", (cutoff,))
        await db.execute("DELETE FROM limits WHERE period_start < ?", (cutoff,))
        await db.execute("DELETE FROM periods WHERE period_start < ?", (cutoff,))
        await db.commit()
