"""Deterministic valuation review, holdings roll-forward, NAV, and bridge."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

from openpyxl import load_workbook

from pydantic import ValidationError

from app.models import Category, Mark, Transaction
from app.money import money, sum_money


@dataclass(frozen=True)
class Q3Valuation:
    company: str
    currency: str
    fair_value_local: Decimal
    fair_value_usd: Decimal
    valuation_method: str


@dataclass(frozen=True)
class Q3Data:
    nav_q3: Decimal
    q3_liabilities: Decimal
    q4_liabilities: Decimal
    costs_usd: dict[str, Decimal]
    valuations: dict[str, Q3Valuation]


@dataclass(frozen=True)
class CloseResult:
    holdings: list[dict[str, Any]]
    review: list[dict[str, Any]]
    nav: dict[str, Any]


def load_q3_data(nav_path: Path, valuation_path: Path) -> Q3Data:
    nav_book = load_workbook(nav_path, data_only=True, read_only=True)
    net_assets = nav_book.worksheets[0]
    nav_q3: Decimal | None = None
    q3_liabilities: Decimal | None = None
    costs: dict[str, Decimal] = {}
    in_holdings = False
    for row in net_assets.iter_rows(values_only=True):
        label = row[0]
        if not isinstance(label, str):
            continue
        if label.startswith("Net asset value"):
            nav_q3 = money(row[1])
        elif label == "Total liabilities":
            q3_liabilities = money(row[1])
        elif label == "Portfolio company":
            in_holdings = True
            continue
        elif in_holdings and label == "Total":
            in_holdings = False
        elif in_holdings:
            costs[label] = money(row[1])
    accruals = nav_book.worksheets[1]
    q4_liabilities: Decimal | None = None
    for row in accruals.iter_rows(values_only=True):
        if row[0] == "Total":
            q4_liabilities = money(row[1])
    if nav_q3 is None or q3_liabilities is None or q4_liabilities is None or not costs:
        raise ValueError("Q3_NAV_INPUT_INVALID")

    valuation_book = load_workbook(valuation_path, data_only=True, read_only=True)
    sheet = valuation_book.worksheets[0]
    valuations: dict[str, Q3Valuation] = {}
    in_table = False
    for row in sheet.iter_rows(values_only=True):
        label = row[0]
        if label == "Portfolio company":
            in_table = True
            continue
        if in_table and label == "Total (USD)":
            break
        if in_table and isinstance(label, str):
            valuations[label] = Q3Valuation(
                company=label, currency=str(row[1]), fair_value_local=money(row[3]),
                fair_value_usd=money(row[5]), valuation_method=str(row[6]),
            )
    if set(costs) != set(valuations):
        raise ValueError("Q3_HOLDINGS_INCONSISTENT")
    return Q3Data(nav_q3, q3_liabilities, q4_liabilities, costs, valuations)


def calculate_close(
    q3: Q3Data,
    transactions: Sequence[Transaction],
    marks: Sequence[dict[str, Any] | Mark],
    eurusd_rate: Decimal | str,
    threshold_pct: Decimal | str,
    valuation_date: date | None = None,
) -> CloseResult:
    fx = Decimal(str(eurusd_rate))
    threshold = Decimal(str(threshold_pct))
    if fx <= 0 or threshold < 0:
        raise ValueError("ASSUMPTION_INVALID")
    if not transactions:
        raise ValueError("TRANSACTIONS_EMPTY")
    unclassified = [row.transaction_id for row in transactions if row.category is None]
    if unclassified:
        raise ValueError(f"UNCATEGORIZED_TRANSACTIONS: {', '.join(unclassified)}")
    try:
        q4_marks = [mark if isinstance(mark, Mark) else Mark.model_validate(mark) for mark in marks]
    except (TypeError, ValidationError, ValueError) as error:
        raise ValueError("Q4_MARK_INVALID") from error
    mark_dates = {mark.valuation_date for mark in q4_marks}
    if valuation_date is None:
        if len(mark_dates) != 1:
            raise ValueError("VALUATION_DATE_REQUIRED")
        valuation_date = next(iter(mark_dates))
    mark_by_company: dict[str, Mark] = {}
    for mark in q4_marks:
        if mark.company in mark_by_company:
            raise ValueError(f"Q4_MARK_DUPLICATE: {mark.company}")
        if mark.currency not in {"USD", "EUR"} or mark.valuation_date != valuation_date:
            raise ValueError(f"Q4_MARK_INVALID: {mark.company}")
        mark_by_company[mark.company] = mark
    closing_cash = transactions[-1].balance
    purchase_rows = [row for row in transactions if row.category == Category.INVESTMENT_PURCHASE]
    sale_rows = [row for row in transactions if row.category == Category.SALE_PROCEEDS]
    holdings_cost = dict(q3.costs_usd)
    for sale in sale_rows:
        if sale.company not in holdings_cost:
            raise ValueError(f"SALE_NOT_HELD: {sale.company}")
        del holdings_cost[sale.company]
    purchase_costs: dict[str, Decimal] = {}
    for purchase in purchase_rows:
        purchase_costs[purchase.company] = money(purchase_costs.get(purchase.company, Decimal("0")) + purchase.debit)
        holdings_cost[purchase.company] = money(holdings_cost.get(purchase.company, Decimal("0")) + purchase.debit)

    holdings: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []
    for company in sorted(holdings_cost):
        q3_mark = q3.valuations.get(company)
        q4_mark = mark_by_company.get(company)
        flags: list[str] = []
        reasons: list[str] = []
        if q4_mark is None:
            if company not in purchase_costs:
                raise ValueError(f"MISSING_MARK_FOR_EXISTING_HOLDING: {company}")
            currency = "USD"
            q4_local = purchase_costs[company]
            q4_fair_usd = purchase_costs[company]
            q4_method = None
            flags.append("R3")
            reasons.append("No Q4 mark; carried at recent Q4 purchase cost.")
        else:
            currency = q4_mark.currency
            q4_local = money(q4_mark.fair_value)
            q4_fair_usd = money(q4_local * (fx if currency == "EUR" else Decimal("1")))
            q4_method = q4_mark.valuation_method
        q3_local = q3_mark.fair_value_local if q3_mark else None
        change_pct: Decimal | None = None
        if q3_mark and q4_mark:
            if q3_mark.currency != currency:
                raise ValueError(f"CURRENCY_CHANGED: {company}")
            change_pct = money((q4_local / q3_mark.fair_value_local - Decimal("1")) * Decimal("100"))
            if abs(change_pct) >= threshold:
                flags.append("R1")
                reasons.append(f"Local-currency fair value changed {change_pct:.2f}% against a {threshold:.2f}% threshold.")
            if q3_mark.valuation_method != q4_method:
                flags.append("R2")
                reasons.append("Valuation method changed from Q3.")
        holdings.append({"company": company, "cost_usd": holdings_cost[company], "fair_value_usd": q4_fair_usd, "q3_fair_value_usd": q3_mark.fair_value_usd if q3_mark else None})
        review.append({
            "company": company, "currency": currency,
            "q3_fair_value": q3_local, "q4_fair_value": q4_local, "change_pct": change_pct,
            "q3_method": q3_mark.valuation_method if q3_mark else None, "q4_method": q4_method,
            "flags": flags, "reason": " ".join(reasons) or "No review flags.",
        })
    for company, q4_mark in sorted(mark_by_company.items()):
        if company not in holdings_cost:
            review.append({"company": company, "currency": q4_mark.currency, "q3_fair_value": None, "q4_fair_value": money(q4_mark.fair_value), "change_pct": None, "q3_method": None, "q4_method": q4_mark.valuation_method, "flags": ["R4"], "reason": "CVP supplied a Q4 mark for a company not held at quarter-end."})

    investments = sum_money(row["fair_value_usd"] for row in holdings)
    capital_called = sum_money(row.credit for row in transactions if row.category == Category.CAPITAL_CALL)
    distributions = sum_money(row.debit for row in transactions if row.category == Category.DISTRIBUTION)
    income = sum_money(row.credit for row in transactions if row.category in {Category.DIVIDEND_INCOME, Category.INTEREST_INCOME})
    management_fee = sum_money(row.debit for row in transactions if row.category == Category.MANAGEMENT_FEE)
    expenses = sum_money([
        sum_money(row.debit for row in transactions if row.category in {Category.MANAGEMENT_FEE, Category.EXPENSE, Category.BANK_CHARGES}),
        -sum_money(row.credit for row in transactions if row.category == Category.EXPENSE_REFUND),
        q3.q4_liabilities,
    ])
    realized_gain = sum_money(row.credit - q3.costs_usd[row.company] for row in sale_rows)
    q3_unrealized = sum_money(mark.fair_value_usd - q3.costs_usd[company] for company, mark in q3.valuations.items())
    q4_unrealized = sum_money(row["fair_value_usd"] - row["cost_usd"] for row in holdings)
    change_unrealized = money(q4_unrealized - q3_unrealized)
    nav_q4 = money(closing_cash + investments - q3.q4_liabilities)
    bridge_value = money(q3.nav_q3 + capital_called - distributions + income - expenses + realized_gain + change_unrealized)
    difference = money(nav_q4 - bridge_value)
    if difference != Decimal("0.00"):
        raise ValueError(f"NAV_BRIDGE_MISMATCH: {difference:.2f}")
    return CloseResult(
        holdings=holdings,
        review=review,
        nav={"closing_cash": closing_cash, "investments_usd": investments, "liabilities": q3.q4_liabilities, "management_fee": management_fee, "nav_q4": nav_q4, "bridge": {
            "nav_q3": q3.nav_q3, "capital_called": capital_called, "distributions": distributions,
            "income": income, "management_fee": management_fee, "expenses": expenses, "realized_gain": realized_gain,
            "change_in_unrealized": change_unrealized, "nav_q4": nav_q4, "difference": difference,
        }},
    )
