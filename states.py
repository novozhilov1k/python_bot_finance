from aiogram.fsm.state import State, StatesGroup


class ExpenseState(StatesGroup):
    waiting_amount = State()
    waiting_category = State()


class EditExpenseState(StatesGroup):
    waiting_amount = State()
    waiting_category = State()


class LimitState(StatesGroup):
    waiting_amount = State()


class CategoryState(StatesGroup):
    waiting_name = State()
    waiting_rename = State()
    waiting_transfer_category = State()


class BudgetState(StatesGroup):
    waiting_start_day = State()


class ExportState(StatesGroup):
    waiting_period = State()
