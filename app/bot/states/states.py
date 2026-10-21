"""Finite State Machine (FSM) StatesGroups for T-Bank Bot."""

from aiogram.fsm.state import State, StatesGroup


class TransferStates(StatesGroup):
    """P2P / SBP Transfer dialogue flow."""
    waiting_for_recipient = State()
    waiting_for_amount = State()
    confirm = State()


class SavingsStates(StatesGroup):
    """18% APR Savings safe deposit & withdrawal flows."""
    waiting_for_deposit_amount = State()
    waiting_for_withdraw_amount = State()


class InvestStates(StatesGroup):
    """T-Investments stock trading buy/sell flows."""
    waiting_for_shares = State()
    confirm_trade = State()


class CashbackStates(StatesGroup):
    """Monthly 4-of-8 category selection."""
    selecting_categories = State()


class AdminStates(StatesGroup):
    """Admin panel operational flows: user search and balance adjustment."""
    search_query = State()
    balance_delta = State()
    balance_reason = State()


class BroadcastStates(StatesGroup):
    """Admin emergency announcement broadcast flow."""
    waiting_for_message = State()
    confirm_broadcast = State()
