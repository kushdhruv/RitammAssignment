"""Position-aware extraction for bank statement PDFs with wrapped descriptions."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import re
from typing import Iterable

import pdfplumber

from app.models import Transaction

DATE_PATTERN = re.compile(r"^(\d{2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})$")
AMOUNT_PATTERN = re.compile(r"^\d{1,3}(?:,\d{3})*\.\d{2}$")


def _words_by_line(words: Iterable[dict]) -> list[list[dict]]:
    grouped: dict[float, list[dict]] = defaultdict(list)
    for word in words:
        grouped[round(float(word["top"]), 1)].append(word)
    return [sorted(grouped[key], key=lambda item: float(item["x0"])) for key in sorted(grouped)]


def _line_date(words: list[dict]) -> str | None:
    leading = [word["text"] for word in words if float(word["x0"]) < 95]
    candidate = " ".join(leading[:3])
    return candidate if DATE_PATTERN.fullmatch(candidate) else None


def _parse_date(value: str) -> datetime.date:
    return datetime.strptime(value, "%d %b %Y").date()


def _amount_at(words: list[dict], lower: float, upper: float) -> Decimal:
    values = [word["text"] for word in words if lower <= float(word["x0"]) < upper and AMOUNT_PATTERN.fullmatch(word["text"])]
    return Decimal(values[-1].replace(",", "")) if values else Decimal("0.00")


def _description_words(words: list[dict]) -> list[str]:
    return [word["text"] for word in words if 95 <= float(word["x0"]) < 308]


def _reference(words: list[dict]) -> str | None:
    values = [word["text"] for word in words if 308 <= float(word["x0"]) < 371]
    return " ".join(values) or None


def parse_bank_statement(path: Path) -> tuple[Decimal, list[Transaction]]:
    """Extract the opening balance and all movement rows, preserving source order."""
    opening_cash: Decimal | None = None
    extracted: list[Transaction] = []
    with pdfplumber.open(path) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            current: dict | None = None
            for line_number, words in enumerate(_words_by_line(page.extract_words()), start=1):
                date_value = _line_date(words)
                if date_value:
                    if current is not None:
                        _append_transaction(current, extracted)
                    current = {
                        "date": _parse_date(date_value),
                        "description": _description_words(words),
                        "reference": _reference(words),
                        "debit": _amount_at(words, 371, 433),
                        "credit": _amount_at(words, 433, 495),
                        "balance": _amount_at(words, 495, 596),
                        "page": page_number,
                        "row": line_number,
                    }
                    continue
                if current is not None:
                    current["description"].extend(_description_words(words))
            if current is not None:
                _append_transaction(current, extracted)

    if not extracted:
        raise ValueError("BANK_EXTRACTION_EMPTY: no statement rows found")
    opening_row = extracted.pop(0)
    if "BALANCE BROUGHT FORWARD" not in opening_row.description.upper():
        raise ValueError("BANK_OPENING_BALANCE_MISSING: expected opening balance row")
    opening_cash = opening_row.balance
    for index, transaction in enumerate(extracted, start=1):
        transaction.transaction_id = f"txn-{index:04d}"
    return opening_cash, extracted


def _append_transaction(current: dict, extracted: list[Transaction]) -> None:
    description = " ".join(current["description"])
    if not description:
        return
    extracted.append(
        Transaction(
            date=current["date"],
            description=description,
            reference=current["reference"],
            debit=current["debit"],
            credit=current["credit"],
            balance=current["balance"],
            source_page=current["page"],
            source_row=current["row"],
        )
    )
