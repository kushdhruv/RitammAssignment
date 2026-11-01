"""Deterministic statement controls that prevent financial finalization on a mismatch."""

from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from app.models import ReconciliationResult, Transaction
from app.money import money, sum_money


def reconcile_bank_statement(opening_cash: Decimal | str, transactions: Sequence[Transaction]) -> ReconciliationResult:
    opening = money(opening_cash)
    expected = opening
    for index, transaction in enumerate(transactions, start=1):
        expected = money(expected + transaction.credit - transaction.debit)
        difference = money(transaction.balance - expected)
        if difference != Decimal("0.00"):
            identifier = transaction.transaction_id or f"txn-{index:04d}"
            raise ValueError(
                f"BANK_RUNNING_BALANCE_MISMATCH {identifier}: expected {expected:.2f}, "
                f"reported {transaction.balance:.2f}, difference {difference:.2f}"
            )
    debits = sum_money(transaction.debit for transaction in transactions)
    credits = sum_money(transaction.credit for transaction in transactions)
    closing = money(opening + credits - debits)
    if transactions and closing != transactions[-1].balance:
        raise ValueError("BANK_STATEMENT_TOTAL_MISMATCH: final balance does not reconcile")
    return ReconciliationResult(
        opening_cash=opening,
        closing_cash=closing,
        total_debits=debits,
        total_credits=credits,
    )
