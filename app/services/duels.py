"""PvP Duels Domain Service: Dice, Coinflip, Russian Roulette, Escrow, and CAS Concurrency Guards."""

import datetime
import random
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Account, LedgerEntry, PvPDuel, Transaction, User
from app.services.receipts import ReceiptService
from app.services.transfers import AccountFrozenError, InsufficientFundsError


@dataclass
class RouletteSoloState:
    """Russian Roulette Solo survival ladder state."""

    user_id: int
    stake: Decimal
    current_step: int  # 0 to 5
    multiplier: int  # 1, 2, 4, 8, 16, 32
    is_alive: bool = True
    current_payout: Decimal = Decimal("0.00")


class DuelsService:
    """Core domain service for PvP Duels and Russian Roulette solo survival."""

    MIN_STAKE: Decimal = Decimal("100.00")
    MAX_STAKE: Decimal = Decimal("20000000.00")
    BANK_COMMISSION_PCT: Decimal = Decimal("0.03")

    @classmethod
    async def _resolve_user_and_account(
        cls, session: AsyncSession, user_id: int
    ) -> tuple[User, Account]:
        """Resolve User and primary MAIN Account by internal User.id or Telegram ID."""
        # 1. Try by internal User.id
        res = await session.execute(
            select(User, Account)
            .join(Account, (Account.user_id == User.id) & (Account.account_type == "MAIN"))
            .where(User.id == user_id)
        )
        row = res.first()
        if row:
            return row[0], row[1]

        # 2. Try by Telegram ID
        res = await session.execute(
            select(User, Account)
            .join(Account, (Account.user_id == User.id) & (Account.account_type == "MAIN"))
            .where(User.telegram_id == user_id)
        )
        row = res.first()
        if row:
            return row[0], row[1]

        raise ValueError(f"Пользователь или основной банковский счет с ID {user_id} не найден.")

    @classmethod
    async def create_duel(
        cls, session: AsyncSession, creator_user_id: int, game_type: str, stake: Decimal
    ) -> PvPDuel:
        """Create a new PvP duel and atomically debit the creator stake into escrow."""
        clean_type = str(game_type).strip().upper()
        if clean_type not in ("DICE", "COINFLIP", "ROULETTE"):
            raise ValueError(
                f"Недопустимый тип игры: {game_type}. Доступны: DICE, COINFLIP, ROULETTE."
            )

        dec_stake = Decimal(str(stake)).quantize(Decimal("0.01"))
        if not (cls.MIN_STAKE <= dec_stake <= cls.MAX_STAKE):
            raise ValueError(
                f"Сумма ставки должна быть от {cls.MIN_STAKE:,.2f} ₽ до {cls.MAX_STAKE:,.2f} ₽."
            )

        creator, main_acc = await cls._resolve_user_and_account(session, creator_user_id)
        if main_acc.is_frozen:
            raise AccountFrozenError("Счет заморожен. Создание дуэли невозможно.")

        # Atomic debit into escrow with balance >= stake guard
        debit_stmt = (
            update(Account)
            .where(Account.id == main_acc.id)
            .where(Account.balance >= dec_stake)
            .where(Account.is_frozen == False)  # noqa: E712
            .values(balance=Account.balance - dec_stake)
        )
        debit_res = await session.execute(debit_stmt)
        if debit_res.rowcount == 0:
            raise InsufficientFundsError(
                f"Недостаточно средств для создания дуэли. Требуется: {dec_stake:,.2f} ₽, на счете: {main_acc.balance:,.2f} ₽"
            )

        new_balance = (
            await session.execute(select(Account.balance).where(Account.id == main_acc.id))
        ).scalar_one()

        tx_id = f"duel-stk-{uuid.uuid4().hex[:12]}"
        tx = Transaction(
            id=tx_id,
            sender_account_id=main_acc.id,
            recipient_account_id=None,
            amount=dec_stake,
            fee=Decimal("0.00"),
            tx_type="DUEL_STAKE",
            category="PvP Дуэли",
            description=f"Ставка в дуэли ({clean_type}): {dec_stake:,.2f} ₽",
            status="COMPLETED",
            fiscal_code=ReceiptService.generate_fiscal_code(),
            auth_code=ReceiptService.generate_auth_code(),
            created_at=datetime.datetime.now(),
        )
        session.add(tx)
        session.add(
            LedgerEntry(
                transaction_id=tx_id,
                account_id=main_acc.id,
                direction="DEBIT",
                amount=dec_stake,
                balance_after=new_balance,
                created_at=datetime.datetime.now(),
            )
        )

        duel = PvPDuel(
            id=str(uuid.uuid4()),
            game_type=clean_type,
            creator_user_id=creator.id,
            stake=dec_stake,
            status="OPEN",
            created_at=datetime.datetime.now(),
        )
        session.add(duel)
        await session.flush()
        return duel

    @classmethod
    async def cancel_duel(
        cls, session: AsyncSession, duel_id: str, user_id: int
    ) -> PvPDuel:
        """Cancel an open duel using CAS and refund the creator's stake."""
        creator, main_acc = await cls._resolve_user_and_account(session, user_id)

        # Atomic CAS: status == "OPEN" and creator_user_id == creator.id
        cancel_stmt = (
            update(PvPDuel)
            .where(PvPDuel.id == duel_id)
            .where(PvPDuel.status == "OPEN")
            .where(PvPDuel.creator_user_id == creator.id)
            .values(status="CANCELLED", finished_at=datetime.datetime.now())
        )
        res = await session.execute(cancel_stmt)
        if res.rowcount == 0:
            raise ValueError("Дуэль не может быть отменена.")

        duel_res = await session.execute(select(PvPDuel).where(PvPDuel.id == duel_id))
        duel = duel_res.scalar_one()

        # Atomically refund creator stake
        credit_stmt = (
            update(Account)
            .where(Account.id == main_acc.id)
            .values(balance=Account.balance + duel.stake)
        )
        await session.execute(credit_stmt)
        new_balance = (
            await session.execute(select(Account.balance).where(Account.id == main_acc.id))
        ).scalar_one()

        tx_id = f"duel-ref-{uuid.uuid4().hex[:12]}"
        tx = Transaction(
            id=tx_id,
            sender_account_id=None,
            recipient_account_id=main_acc.id,
            amount=duel.stake,
            fee=Decimal("0.00"),
            tx_type="DUEL_REFUND",
            category="PvP Дуэли",
            description=f"Возврат ставки за отмененную дуэль ({duel.game_type}): {duel.stake:,.2f} ₽",
            status="COMPLETED",
            fiscal_code=ReceiptService.generate_fiscal_code(),
            auth_code=ReceiptService.generate_auth_code(),
            created_at=datetime.datetime.now(),
        )
        session.add(tx)
        session.add(
            LedgerEntry(
                transaction_id=tx_id,
                account_id=main_acc.id,
                direction="CREDIT",
                amount=duel.stake,
                balance_after=new_balance,
                created_at=datetime.datetime.now(),
            )
        )
        await session.flush()
        return duel

    @classmethod
    async def get_open_duels(
        cls, session: AsyncSession, game_type: str | None = None
    ) -> list[PvPDuel]:
        """Fetch all open duels waiting for an opponent."""
        query = select(PvPDuel).where(PvPDuel.status == "OPEN")
        if game_type:
            query = query.where(PvPDuel.game_type == game_type.strip().upper())
        query = query.order_by(PvPDuel.created_at.desc())
        res = await session.execute(query)
        return list(res.scalars().all())

    @classmethod
    async def get_duel_by_id(cls, session: AsyncSession, duel_id: str) -> PvPDuel | None:
        """Fetch duel by unique ID."""
        res = await session.execute(select(PvPDuel).where(PvPDuel.id == duel_id))
        return res.scalars().first()

    @classmethod
    async def get_user_duels(
        cls, session: AsyncSession, user_id: int, limit: int = 20
    ) -> list[PvPDuel]:
        """Fetch duels created or participated by the specified user."""
        user, _ = await cls._resolve_user_and_account(session, user_id)
        query = (
            select(PvPDuel)
            .where((PvPDuel.creator_user_id == user.id) | (PvPDuel.opponent_user_id == user.id))
            .order_by(PvPDuel.created_at.desc())
            .limit(limit)
        )
        res = await session.execute(query)
        return list(res.scalars().all())

    @classmethod
    async def accept_duel(
        cls,
        session: AsyncSession,
        duel_id: str,
        opponent_user_id: int,
        *,
        force_creator_roll: int | None = None,
        force_opponent_roll: int | None = None,
        force_coinflip: int | None = None,
        force_bullet_chamber: int | None = None,
    ) -> tuple[PvPDuel, dict[str, Any]]:
        """Accept an open duel with CAS concurrency guard, debit opponent, resolve winner, and credit pot."""
        opp_user, opp_acc = await cls._resolve_user_and_account(session, opponent_user_id)
        if opp_acc.is_frozen:
            raise AccountFrozenError("Счет заморожен. Принятие дуэли невозможно.")

        # Concurrency Guard: Atomic CAS
        cas_stmt = (
            update(PvPDuel)
            .where(PvPDuel.id == duel_id)
            .where(PvPDuel.status == "OPEN")
            .where(PvPDuel.creator_user_id != opp_user.id)
            .values(status="ACCEPTED", opponent_user_id=opp_user.id)
        )
        cas_res = await session.execute(cas_stmt)
        if cas_res.rowcount == 0:
            # Check if duel exists or was created by opponent
            duel_check = (
                await session.execute(select(PvPDuel).where(PvPDuel.id == duel_id))
            ).scalar_one_or_none()
            if duel_check and duel_check.creator_user_id == opp_user.id:
                raise ValueError("Нельзя принять собственную дуэль!")
            raise ValueError("Дуэль уже принята другим игроком или недоступна.")

        duel_res = await session.execute(select(PvPDuel).where(PvPDuel.id == duel_id))
        duel = duel_res.scalar_one()
        stake = duel.stake

        # Debit opponent stake into escrow
        opp_debit = (
            update(Account)
            .where(Account.id == opp_acc.id)
            .where(Account.balance >= stake)
            .where(Account.is_frozen == False)  # noqa: E712
            .values(balance=Account.balance - stake)
        )
        debit_res = await session.execute(opp_debit)
        if debit_res.rowcount == 0:
            # Revert duel status to OPEN
            await session.execute(
                update(PvPDuel)
                .where(PvPDuel.id == duel_id)
                .values(status="OPEN", opponent_user_id=None)
            )
            await session.flush()
            raise InsufficientFundsError(
                f"Недостаточно средств для принятия дуэли. Требуется: {stake:,.2f} ₽"
            )

        opp_new_bal = (
            await session.execute(select(Account.balance).where(Account.id == opp_acc.id))
        ).scalar_one()

        # Record opponent Transaction and LedgerEntry
        opp_tx_id = f"duel-stk-{uuid.uuid4().hex[:12]}"
        opp_tx = Transaction(
            id=opp_tx_id,
            sender_account_id=opp_acc.id,
            recipient_account_id=None,
            amount=stake,
            fee=Decimal("0.00"),
            tx_type="DUEL_STAKE",
            category="PvP Дуэли",
            description=f"Ставка в дуэли ({duel.game_type}): {stake:,.2f} ₽",
            status="COMPLETED",
            fiscal_code=ReceiptService.generate_fiscal_code(),
            auth_code=ReceiptService.generate_auth_code(),
            created_at=datetime.datetime.now(),
        )
        session.add(opp_tx)
        session.add(
            LedgerEntry(
                transaction_id=opp_tx_id,
                account_id=opp_acc.id,
                direction="DEBIT",
                amount=stake,
                balance_after=opp_new_bal,
                created_at=datetime.datetime.now(),
            )
        )

        creator_user, creator_acc = await cls._resolve_user_and_account(
            session, duel.creator_user_id
        )

        pot = stake * 2
        commission = (pot * cls.BANK_COMMISSION_PCT).quantize(Decimal("0.01"))
        winner_payout = pot - commission
        loser_id = None
        result_details: dict[str, Any] = {
            "duel_id": duel.id,
            "game_type": duel.game_type,
            "stake": stake,
            "pot": pot,
            "creator_user_id": creator_user.id,
            "opponent_user_id": opp_user.id,
        }

        # Resolve game
        if duel.game_type == "DICE":
            c_roll = force_creator_roll if force_creator_roll is not None else random.randint(1, 6)
            o_roll = force_opponent_roll if force_opponent_roll is not None else random.randint(1, 6)
            duel.creator_roll = c_roll
            duel.opponent_roll = o_roll

            if c_roll > o_roll:
                winner_id = creator_user.id
                loser_id = opp_user.id
                is_draw = False
            elif o_roll > c_roll:
                winner_id = opp_user.id
                loser_id = creator_user.id
                is_draw = False
            else:
                winner_id = None
                is_draw = True

            result_details.update({
                "creator_roll": c_roll,
                "opponent_roll": o_roll,
                "is_draw": is_draw,
                "winner_user_id": winner_id,
            })

        elif duel.game_type == "COINFLIP":
            flip = force_coinflip if force_coinflip is not None else random.choice([1, 2])
            # 1 = Heads (Creator wins), 2 = Tails (Opponent wins)
            duel.creator_roll = flip
            duel.opponent_roll = 1 if flip == 2 else 2
            is_draw = False
            if flip == 1:
                winner_id = creator_user.id
                loser_id = opp_user.id
            else:
                winner_id = opp_user.id
                loser_id = creator_user.id

            result_details.update({
                "flip": flip,
                "side": "ОРЕЛ" if flip == 1 else "РЕШКА",
                "is_draw": False,
                "winner_user_id": winner_id,
            })

        elif duel.game_type == "ROULETTE":
            # 2-player Russian Roulette
            chamber = (
                force_bullet_chamber
                if force_bullet_chamber is not None
                else random.randint(1, 6)
            )
            duel.creator_roll = chamber
            duel.opponent_roll = 6
            is_draw = False

            # Turn 1, 3, 5 = Creator. Turn 2, 4, 6 = Opponent.
            if chamber % 2 == 1:
                # Creator hits bullet
                winner_id = opp_user.id
                loser_id = creator_user.id
                fatal_player = "CREATOR"
            else:
                # Opponent hits bullet
                winner_id = creator_user.id
                loser_id = opp_user.id
                fatal_player = "OPPONENT"

            result_details.update({
                "bullet_chamber": chamber,
                "fatal_turn": chamber,
                "fatal_player": fatal_player,
                "is_draw": False,
                "winner_user_id": winner_id,
                "loser_user_id": loser_id,
            })
        else:
            raise ValueError(f"Неизвестный тип игры: {duel.game_type}")

        if not is_draw:
            winner_acc = creator_acc if winner_id == creator_user.id else opp_acc
            credit_stmt = (
                update(Account)
                .where(Account.id == winner_acc.id)
                .values(balance=Account.balance + winner_payout)
            )
            await session.execute(credit_stmt)
            w_bal = (
                await session.execute(select(Account.balance).where(Account.id == winner_acc.id))
            ).scalar_one()

            win_tx_id = f"duel-win-{uuid.uuid4().hex[:12]}"
            win_tx = Transaction(
                id=win_tx_id,
                sender_account_id=None,
                recipient_account_id=winner_acc.id,
                amount=winner_payout,
                fee=commission,
                tx_type="DUEL_WIN",
                category="PvP Дуэли",
                description=f"Победа в дуэли ({duel.game_type}): банк {pot:,.2f} ₽, комиссия 3% ({commission:,.2f} ₽)",
                status="COMPLETED",
                fiscal_code=ReceiptService.generate_fiscal_code(),
                auth_code=ReceiptService.generate_auth_code(),
                created_at=datetime.datetime.now(),
            )
            session.add(win_tx)
            session.add(
                LedgerEntry(
                    transaction_id=win_tx_id,
                    account_id=winner_acc.id,
                    direction="CREDIT",
                    amount=winner_payout,
                    balance_after=w_bal,
                    created_at=datetime.datetime.now(),
                )
            )
            duel.status = "FINISHED"
            duel.winner_user_id = winner_id
            duel.finished_at = datetime.datetime.now()
            result_details.update({
                "commission": commission,
                "payout": winner_payout,
            })
        else:
            # DRAW: refund both creator and opponent
            # Creator refund
            await session.execute(
                update(Account)
                .where(Account.id == creator_acc.id)
                .values(balance=Account.balance + stake)
            )
            c_bal = (
                await session.execute(select(Account.balance).where(Account.id == creator_acc.id))
            ).scalar_one()
            c_tx_id = f"duel-ref-{uuid.uuid4().hex[:12]}"
            session.add(
                Transaction(
                    id=c_tx_id,
                    sender_account_id=None,
                    recipient_account_id=creator_acc.id,
                    amount=stake,
                    fee=Decimal("0.00"),
                    tx_type="DUEL_REFUND",
                    category="PvP Дуэли",
                    description=f"Ничья в дуэли ({duel.game_type}): возврат ставки {stake:,.2f} ₽",
                    status="COMPLETED",
                    fiscal_code=ReceiptService.generate_fiscal_code(),
                    auth_code=ReceiptService.generate_auth_code(),
                    created_at=datetime.datetime.now(),
                )
            )
            session.add(
                LedgerEntry(
                    transaction_id=c_tx_id,
                    account_id=creator_acc.id,
                    direction="CREDIT",
                    amount=stake,
                    balance_after=c_bal,
                    created_at=datetime.datetime.now(),
                )
            )

            # Opponent refund
            await session.execute(
                update(Account)
                .where(Account.id == opp_acc.id)
                .values(balance=Account.balance + stake)
            )
            o_bal = (
                await session.execute(select(Account.balance).where(Account.id == opp_acc.id))
            ).scalar_one()
            o_tx_id = f"duel-ref-{uuid.uuid4().hex[:12]}"
            session.add(
                Transaction(
                    id=o_tx_id,
                    sender_account_id=None,
                    recipient_account_id=opp_acc.id,
                    amount=stake,
                    fee=Decimal("0.00"),
                    tx_type="DUEL_REFUND",
                    category="PvP Дуэли",
                    description=f"Ничья в дуэли ({duel.game_type}): возврат ставки {stake:,.2f} ₽",
                    status="COMPLETED",
                    fiscal_code=ReceiptService.generate_fiscal_code(),
                    auth_code=ReceiptService.generate_auth_code(),
                    created_at=datetime.datetime.now(),
                )
            )
            session.add(
                LedgerEntry(
                    transaction_id=o_tx_id,
                    account_id=opp_acc.id,
                    direction="CREDIT",
                    amount=stake,
                    balance_after=o_bal,
                    created_at=datetime.datetime.now(),
                )
            )

            duel.status = "FINISHED"
            duel.winner_user_id = None
            duel.finished_at = datetime.datetime.now()
            result_details.update({
                "commission": Decimal("0.00"),
                "payout": stake,
            })

        await session.flush()
        return duel, result_details

    @classmethod
    async def start_roulette_solo(
        cls, session: AsyncSession, user_id: int, stake: Decimal
    ) -> RouletteSoloState:
        """Start a Russian Roulette solo survival session and debit stake upfront."""
        dec_stake = Decimal(str(stake)).quantize(Decimal("0.01"))
        if not (cls.MIN_STAKE <= dec_stake <= cls.MAX_STAKE):
            raise ValueError(
                f"Сумма ставки должна быть от {cls.MIN_STAKE:,.2f} ₽ до {cls.MAX_STAKE:,.2f} ₽."
            )

        user, main_acc = await cls._resolve_user_and_account(session, user_id)
        if main_acc.is_frozen:
            raise AccountFrozenError("Счет заморожен. Игра в рулетку невозможна.")

        # Atomic debit upfront
        debit_stmt = (
            update(Account)
            .where(Account.id == main_acc.id)
            .where(Account.balance >= dec_stake)
            .where(Account.is_frozen == False)  # noqa: E712
            .values(balance=Account.balance - dec_stake)
        )
        debit_res = await session.execute(debit_stmt)
        if debit_res.rowcount == 0:
            raise InsufficientFundsError(
                f"Недостаточно средств для участия в Русской рулетке. Требуется: {dec_stake:,.2f} ₽"
            )

        new_balance = (
            await session.execute(select(Account.balance).where(Account.id == main_acc.id))
        ).scalar_one()

        tx_id = f"rr-stk-{uuid.uuid4().hex[:12]}"
        tx = Transaction(
            id=tx_id,
            sender_account_id=main_acc.id,
            recipient_account_id=None,
            amount=dec_stake,
            fee=Decimal("0.00"),
            tx_type="DUEL_STAKE",
            category="PvP Дуэли",
            description=f"Русская рулетка Solo: ставка {dec_stake:,.2f} ₽",
            status="COMPLETED",
            fiscal_code=ReceiptService.generate_fiscal_code(),
            auth_code=ReceiptService.generate_auth_code(),
            created_at=datetime.datetime.now(),
        )
        session.add(tx)
        session.add(
            LedgerEntry(
                transaction_id=tx_id,
                account_id=main_acc.id,
                direction="DEBIT",
                amount=dec_stake,
                balance_after=new_balance,
                created_at=datetime.datetime.now(),
            )
        )
        await session.flush()
        return RouletteSoloState(
            user_id=user.id,
            stake=dec_stake,
            current_step=0,
            multiplier=1,
            is_alive=True,
            current_payout=Decimal("0.00"),
        )

    @classmethod
    async def roulette_solo_pull(
        cls,
        session: AsyncSession,
        state: RouletteSoloState,
        force_outcome: bool | None = None,
    ) -> tuple[RouletteSoloState, bool]:
        """Pull the trigger in Russian Roulette Solo. Returns (updated_state, survived)."""
        if not state.is_alive:
            raise ValueError("Игрок уже выбыл. Раунд окончен.")
        if state.current_step >= 5:
            raise ValueError("Достигнут максимальный шаг (шаг 5, x32)! Выполните cashout.")

        if force_outcome is not None:
            survived = bool(force_outcome)
        else:
            # 1 bullet in 6 chambers -> 5/6 survival probability
            chamber = random.randint(1, 6)
            survived = chamber != 1

        if survived:
            state.current_step += 1
            state.multiplier = 2**state.current_step
            state.current_payout = (state.stake * Decimal(str(state.multiplier))).quantize(
                Decimal("0.01")
            )
            return state, True
        else:
            state.is_alive = False
            state.current_payout = Decimal("0.00")
            return state, False

    @classmethod
    async def roulette_solo_cashout(
        cls, session: AsyncSession, state: RouletteSoloState
    ) -> Decimal:
        """Cash out accumulated multiplier winnings in Russian Roulette Solo."""
        if not state.is_alive:
            raise ValueError("Невозможно забрать выигрыш: игрок выбыл.")
        if state.current_step == 0:
            raise ValueError("Невозможно забрать выигрыш: не сделано ни одного выстрела.")

        payout = (state.stake * Decimal(str(state.multiplier))).quantize(Decimal("0.01"))
        user, main_acc = await cls._resolve_user_and_account(session, state.user_id)

        credit_stmt = (
            update(Account)
            .where(Account.id == main_acc.id)
            .values(balance=Account.balance + payout)
        )
        await session.execute(credit_stmt)
        new_balance = (
            await session.execute(select(Account.balance).where(Account.id == main_acc.id))
        ).scalar_one()

        tx_id = f"rr-win-{uuid.uuid4().hex[:12]}"
        tx = Transaction(
            id=tx_id,
            sender_account_id=None,
            recipient_account_id=main_acc.id,
            amount=payout,
            fee=Decimal("0.00"),
            tx_type="DUEL_WIN",
            category="PvP Дуэли",
            description=f"Русская рулетка Solo (шаг {state.current_step}, x{state.multiplier}): выигрыш {payout:,.2f} ₽",
            status="COMPLETED",
            fiscal_code=ReceiptService.generate_fiscal_code(),
            auth_code=ReceiptService.generate_auth_code(),
            created_at=datetime.datetime.now(),
        )
        session.add(tx)
        session.add(
            LedgerEntry(
                transaction_id=tx_id,
                account_id=main_acc.id,
                direction="CREDIT",
                amount=payout,
                balance_after=new_balance,
                created_at=datetime.datetime.now(),
            )
        )
        state.is_alive = False
        state.current_payout = Decimal("0.00")
        await session.flush()
        return payout


__all__ = [
    "DuelsService",
    "RouletteSoloState",
    "AccountFrozenError",
    "InsufficientFundsError",
]
