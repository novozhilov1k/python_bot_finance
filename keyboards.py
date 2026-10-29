from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from services import format_date, format_money


def main_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить трату", callback_data="add_expense")
    builder.button(text="📈 Статистика", callback_data="stats")
    builder.button(text="🎯 Лимиты", callback_data="limits")
    builder.button(text="📂 Категории", callback_data="categories")
    builder.button(text="📤 Экспорт", callback_data="export")
    builder.button(text="🔄 Бюджеты", callback_data="switch_budget")
    builder.adjust(2)
    return builder.as_markup()


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ В главное меню", callback_data="main_menu")
    return builder.as_markup()


def budgets_keyboard(
    budgets,
    active_id: int | None,
    can_create_shared: bool,
    can_invite: bool,
    can_settings: bool,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for budget in budgets:
        mark = "✅ " if budget["id"] == active_id else ""
        kind = "личный" if budget["type"] == "personal" else "общий"
        builder.button(
            text=f"{mark}{budget['name']} ({kind})",
            callback_data=f"set_budget_{budget['id']}",
        )
    if can_create_shared:
        builder.button(text="➕ Создать общий бюджет", callback_data="create_shared_budget")
    if can_invite:
        builder.button(text="👥 Пригласить участника", callback_data="invite_member")
    if can_settings:
        builder.button(text="⚙️ День начала периода", callback_data="change_start_day")
    builder.button(text="⬅️ В главное меню", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()


def categories_keyboard(categories, prefix: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for category in categories:
        builder.button(text=category["name"], callback_data=f"{prefix}{category['id']}")
    builder.button(text="⬅️ В главное меню", callback_data="main_menu")
    builder.adjust(2, 1)
    return builder.as_markup()


def limit_targets_keyboard(categories) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Общий лимит (на все траты)", callback_data="lim_cat_general")
    for category in categories:
        builder.button(text=category["name"], callback_data=f"lim_cat_{category['id']}")
    builder.button(text="⬅️ Назад", callback_data="limits")
    builder.adjust(1)
    return builder.as_markup()


def stats_keyboard(categories) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for category in categories:
        builder.button(text=category["name"], callback_data=f"stats_cat_{category['id']}")
    builder.button(text="⬅️ В главное меню", callback_data="main_menu")
    builder.adjust(2, 1)
    return builder.as_markup()


def expense_list_keyboard(expenses) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for expense in expenses:
        builder.button(
            text=f"{format_date(expense['spent_on'])} — {format_money(expense['amount'])} ₽",
            callback_data=f"view_exp_{expense['id']}",
        )
    builder.button(text="⬅️ К статистике", callback_data="stats")
    builder.adjust(1)
    return builder.as_markup()


def expense_detail_keyboard(expense_id: int, category_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Изменить сумму", callback_data=f"edit_amount_{expense_id}")
    builder.button(text="📂 Изменить категорию", callback_data=f"edit_cat_{expense_id}")
    builder.button(text="🗑 Удалить", callback_data=f"del_exp_{expense_id}")
    builder.button(text="⬅️ К списку трат", callback_data=f"stats_cat_{category_id}")
    builder.adjust(1)
    return builder.as_markup()


def limits_keyboard(limits, categories) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    names = {category["id"]: category["name"] for category in categories}
    for limit in limits:
        if limit["category_id"] is None:
            builder.button(
                text=f"✏️ Общий лимит: {format_money(limit['amount'])} ₽",
                callback_data="lim_edit_general",
            )
        else:
            name = names.get(limit["category_id"], "?")
            builder.button(
                text=f"✏️ {name}: {format_money(limit['amount'])} ₽",
                callback_data=f"lim_edit_{limit['category_id']}",
            )
    builder.button(text="➕ Добавить лимит", callback_data="lim_add")
    builder.button(text="⬅️ В главное меню", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()


def categories_manage_keyboard(categories) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for category in categories:
        builder.button(text=f"✏️ {category['name']}", callback_data=f"ren_cat_{category['id']}")
        builder.button(text="🗑", callback_data=f"del_cat_{category['id']}")
    builder.button(text="➕ Создать категорию", callback_data="create_category")
    builder.button(text="⬅️ В главное меню", callback_data="main_menu")
    builder.adjust(2)
    return builder.as_markup()


def transfer_category_keyboard(categories, exclude_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for category in categories:
        if category["id"] != exclude_id:
            builder.button(
                text=category["name"],
                callback_data=f"transfer_{exclude_id}_{category['id']}",
            )
    builder.button(text="❌ Отмена", callback_data="categories")
    builder.adjust(2)
    return builder.as_markup()


def export_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📅 Текущий период", callback_data="export_current")
    builder.button(text="📅 Прошлый период", callback_data="export_prev")
    builder.button(text="⬅️ В главное меню", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()


def new_period_keyboard(budget_id: int, period_start: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Да, как в прошлом",
        callback_data=f"confirm_limits_{budget_id}_{period_start}",
    )
    builder.button(
        text="✏️ Изменить лимиты",
        callback_data=f"change_limits_{budget_id}_{period_start}",
    )
    builder.adjust(1)
    return builder.as_markup()
