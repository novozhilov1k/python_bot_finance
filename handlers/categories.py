from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from access import resolve_budget, resolve_user
from database import (
    create_category,
    delete_category,
    find_category_by_name,
    get_categories,
    get_category,
    rename_category,
)
from keyboards import (
    back_to_menu_keyboard,
    categories_manage_keyboard,
    transfer_category_keyboard,
)
from states import CategoryState
from ui import safe_edit

router = Router()
MAX_NAME_LENGTH = 40


async def _validate_name(message: Message, name: str) -> str | None:
    if not name:
        await message.answer("Название не может быть пустым.")
        return None
    if len(name) > MAX_NAME_LENGTH:
        await message.answer(f"Слишком длинное название (максимум {MAX_NAME_LENGTH} символов).")
        return None
    return name


@router.callback_query(F.data == "categories")
async def show_categories(callback: CallbackQuery, state: FSMContext) -> None:
    user = await resolve_user(callback)
    budget = await resolve_budget(user)
    if not budget:
        await callback.answer("Сначала отправьте /start", show_alert=True)
        return

    await state.clear()
    categories = await get_categories(budget["id"])
    if not categories:
        text = "📂 Категорий пока нет. Создайте первую — без неё нельзя записать трату."
    else:
        text = "📂 Ваши категории:\n\n✏️ — переименовать, 🗑 — удалить."
    await safe_edit(callback.message, text, categories_manage_keyboard(categories))
    await callback.answer()


@router.callback_query(F.data == "create_category")
async def create_category_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CategoryState.waiting_name)
    await safe_edit(callback.message, "Введите название новой категории:")
    await callback.answer()


@router.message(CategoryState.waiting_name)
async def create_category_process(message: Message, state: FSMContext) -> None:
    name = await _validate_name(message, (message.text or "").strip())
    if not name:
        return

    user = await resolve_user(message)
    budget = await resolve_budget(user)
    if not budget:
        await state.clear()
        await message.answer("Сначала отправьте /start.")
        return

    if await find_category_by_name(budget["id"], name):
        await message.answer("Такая категория уже есть. Введите другое название.")
        return

    await create_category(budget["id"], name)
    await state.clear()
    await message.answer("✅ Категория создана.", reply_markup=back_to_menu_keyboard())


@router.callback_query(F.data.startswith("ren_cat_"))
async def rename_category_start(callback: CallbackQuery, state: FSMContext) -> None:
    category_id = int(callback.data.removeprefix("ren_cat_"))
    category = await get_category(category_id)
    if not category:
        await callback.answer("Категория не найдена", show_alert=True)
        return

    await state.set_state(CategoryState.waiting_rename)
    await state.update_data(category_id=category_id)
    await safe_edit(
        callback.message,
        f"Текущее название: {category['name']}\nВведите новое название:",
    )
    await callback.answer()


@router.message(CategoryState.waiting_rename)
async def rename_category_process(message: Message, state: FSMContext) -> None:
    name = await _validate_name(message, (message.text or "").strip())
    if not name:
        return

    data = await state.get_data()
    category_id = data.get("category_id")
    if not category_id:
        await state.clear()
        await message.answer("Сессия устарела, отправьте /start.")
        return

    category = await get_category(category_id)
    if not category:
        await state.clear()
        await message.answer("Категория не найдена.", reply_markup=back_to_menu_keyboard())
        return

    duplicate = await find_category_by_name(category["budget_id"], name)
    if duplicate and duplicate["id"] != category_id:
        await message.answer("Такая категория уже есть. Введите другое название.")
        return

    await rename_category(category_id, name)
    await state.clear()
    await message.answer("✅ Категория переименована.", reply_markup=back_to_menu_keyboard())


@router.callback_query(F.data.startswith("del_cat_"))
async def delete_category_start(callback: CallbackQuery, state: FSMContext) -> None:
    category_id = int(callback.data.removeprefix("del_cat_"))
    user = await resolve_user(callback)
    budget = await resolve_budget(user)
    category = await get_category(category_id)
    if not budget or not category or category["budget_id"] != budget["id"]:
        await callback.answer("Категория не найдена", show_alert=True)
        return

    categories = await get_categories(budget["id"])
    others = [item for item in categories if item["id"] != category_id]
    if not others:
        await safe_edit(
            callback.message,
            "Нельзя удалить единственную категорию — траты некуда будет перенести.\n"
            "Сначала создайте другую категорию.",
            back_to_menu_keyboard(),
        )
        await callback.answer()
        return

    await safe_edit(
        callback.message,
        f"Удаляем «{category['name']}».\nКуда перенести траты из этой категории?",
        transfer_category_keyboard(categories, category_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("transfer_"))
async def transfer_and_delete(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split("_")
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await callback.answer("Кнопка устарела", show_alert=True)
        return

    from_id = int(parts[1])
    to_id = int(parts[2])
    user = await resolve_user(callback)
    budget = await resolve_budget(user)
    source = await get_category(from_id)
    target = await get_category(to_id)
    if (
        not budget
        or not source
        or not target
        or source["budget_id"] != budget["id"]
        or target["budget_id"] != budget["id"]
    ):
        await callback.answer("Категория не найдена", show_alert=True)
        return

    await delete_category(from_id, to_id)
    await state.clear()
    await safe_edit(
        callback.message,
        f"✅ Категория «{source['name']}» удалена, траты перенесены в «{target['name']}».",
        back_to_menu_keyboard(),
    )
    await callback.answer("Удалено")
