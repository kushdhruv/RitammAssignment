"""Validated domain records shared across extraction, A2A, and accounting."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator


class Category(StrEnum):
    CAPITAL_CALL = "capital_call"
    DISTRIBUTION = "distribution"
    MANAGEMENT_FEE = "management_fee"
    INVESTMENT_PURCHASE = "investment_purchase"
    SALE_PROCEEDS = "sale_proceeds"
    DIVIDEND_INCOME = "dividend_income"
    INTEREST_INCOME = "interest_income"
    BANK_CHARGES = "bank_charges"
    EXPENSE = "expense"
    EXPENSE_REFUND = "expense_refund"
    ACCRUAL_SETTLEMENT = "accrual_settlement"


class Transaction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_id: str = ""
    date: date
    description: str = Field(min_length=1)
    reference: str | None = None
    debit: Decimal
    credit: Decimal
    balance: Decimal
    category: Category | None = None
    lp_code: str | None = None
    company: str | None = None
    classification_override_reason: str | None = None
    source_page: int | None = None
    source_row: int | None = None

    @field_validator("debit", "credit", "balance", mode="before")
    @classmethod
    def parse_amount(cls, value: Any) -> Decimal:
        if isinstance(value, Decimal):
            amount = value
        else:
            amount = Decimal(str(value).replace(",", ""))
        if not amount.is_finite() or amount * 100 != (amount * 100).to_integral_value():
            raise ValueError("amount must be finite and have at most two decimal places")
        return amount.quantize(Decimal("0.01"))

    @field_validator("reference", "lp_code", "company", mode="before")
    @classmethod
    def blank_to_none(cls, value: Any) -> Any:
        return None if value is None or str(value).strip() == "" else str(value).strip()


class ReconciliationResult(BaseModel):
    opening_cash: Decimal
    closing_cash: Decimal
    total_debits: Decimal
    total_credits: Decimal


class ActivityItem(BaseModel):
    company: str
    date: date
    amount_usd: Decimal | None = None
    proceeds_usd: Decimal | None = None


class Mark(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: str = Field(min_length=1)
    currency: str = Field(min_length=3, max_length=3)
    fair_value: Decimal
    valuation_method: str
    valuation_date: date

    @field_validator("fair_value", mode="before")
    @classmethod
    def validate_fair_value(cls, value: Any) -> Decimal:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount * 100 != (amount * 100).to_integral_value():
            raise ValueError("fair value must be finite, non-negative, and have at most two decimal places")
        return amount


class HoldingsRow(BaseModel):
    company: str
    cost_usd: Decimal
    fair_value_usd: Decimal
    q3_fair_value_usd: Decimal | None = None

