"""LLM-backed transaction classification with strict deterministic validation."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import time
from typing import Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.models import Category, Transaction


class Classifier(Protocol):
    def classify(self, transactions: Sequence[Transaction]) -> list[dict]: ...


LP_DIRECTORY = {
    "LAKESHORE": "LP01", "FJELLHEIM": "LP02", "ST PENSION": "LP03", "KESTREL": "LP04",
    "THORNVALE": "LP05", "PACIFICA": "LP06", "BLUEWATER": "LP07", "MARLOW": "LP08",
    "COASTLINE": "LP09", "MEHTA": "LP10",
}
COMPANY_DIRECTORY = {
    "AURORA LOGISTICS": "Aurora Logistics Pvt Ltd",
    "FJORD PAYMENTS": "Fjord Payments AS",
    "IONMERE MATERIALS": "Ionmere Materials Ltd",
}
INCOMING_CATEGORIES = {Category.CAPITAL_CALL, Category.SALE_PROCEEDS, Category.DIVIDEND_INCOME, Category.INTEREST_INCOME, Category.EXPENSE_REFUND}
OUTGOING_CATEGORIES = set(Category) - INCOMING_CATEGORIES


@dataclass(frozen=True)
class OpenAICompatibleClassifier:
    api_key: str
    base_url: str
    model: str
    timeout_seconds: float = 30.0
    attempts: int = 3

    @classmethod
    def from_environment(cls) -> "OpenAICompatibleClassifier":
        values = {name: os.getenv(name, "").strip() for name in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL")}
        missing = [name for name, value in values.items() if not value or value.startswith("your_")]
        if missing:
            raise ValueError(f"LLM_CONFIGURATION_MISSING: {', '.join(missing)}")
        return cls(values["LLM_API_KEY"], values["LLM_BASE_URL"], values["LLM_MODEL"])

    def classify(self, transactions: Sequence[Transaction]) -> list[dict]:
        ledger = [
            {"transaction_id": row.transaction_id, "description": row.description, "reference": row.reference,
             "debit": str(row.debit), "credit": str(row.credit)}
            for row in transactions
        ]
        prompt = (
            "Classify EVERY bank transaction. Return JSON only: an object with a 'classifications' array. "
            "Each array item has transaction_id, category, lp_code, company. Categories: "
            + ", ".join(category.value for category in Category)
            + ". lp_code is required for capital_call/distribution and company is required for "
            "investment_purchase/sale_proceeds. Preserve no amounts.\nTransactions:\n"
            + json.dumps(ledger)
        )
        payload = {"model": self.model, "temperature": 0, "messages": [
            {"role": "system", "content": "You are a precise fund-accounting transaction classifier."},
            {"role": "user", "content": prompt},
        ]}
        endpoint = self.base_url.rstrip("/") + "/chat/completions"
        last_error: Exception | None = None
        for attempt in range(self.attempts):
            try:
                request = Request(endpoint, data=json.dumps(payload).encode("utf-8"), method="POST", headers={
                    "Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
                })
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    result = json.loads(response.read().decode("utf-8"))
                content = result["choices"][0]["message"]["content"]
                decoded = json.loads(_strip_code_fence(content))
                return decoded["classifications"]
            except HTTPError as error:
                last_error = error
                if error.code not in {408, 429, 500, 502, 503, 504}:
                    break
                if attempt + 1 < self.attempts:
                    time.sleep(2**attempt)
            except (URLError, TimeoutError, KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
                last_error = error
                if attempt + 1 < self.attempts:
                    time.sleep(2**attempt)
        raise RuntimeError(f"LLM_CLASSIFICATION_FAILED after {self.attempts} attempts: {last_error}")


class DeterministicClassifier:
    """Explicit local demo/test adapter. Do not use this in evaluator mode."""

    def classify(self, transactions: Sequence[Transaction]) -> list[dict]:
        return [_classify_by_rules(row) for row in transactions]


def classify_transactions(transactions: Sequence[Transaction], classifier: Classifier) -> list[Transaction]:
    responses = classifier.classify(transactions)
    by_id: dict[str, dict] = {}
    expected = {row.transaction_id for row in transactions}
    for response in responses:
        if not isinstance(response, dict) or response.get("transaction_id") not in expected:
            raise ValueError("LLM_RESPONSE_INVALID: unknown or malformed transaction_id")
        identifier = response["transaction_id"]
        if identifier in by_id:
            raise ValueError(f"LLM_RESPONSE_DUPLICATE: {identifier}")
        by_id[identifier] = response
    missing = expected - set(by_id)
    if missing:
        raise ValueError(f"LLM_RESPONSE_MISSING: {', '.join(sorted(missing))}")

    classified: list[Transaction] = []
    for row in transactions:
        response = by_id[row.transaction_id]
        try:
            category = Category(response["category"])
        except (KeyError, ValueError) as error:
            raise ValueError(f"LLM_CATEGORY_INVALID: {row.transaction_id}") from error
        lp_code = _clean_optional(response.get("lp_code"))
        company = _clean_optional(response.get("company"))
        if category in {Category.CAPITAL_CALL, Category.DISTRIBUTION} and not lp_code:
            raise ValueError(f"LLM_LP_CODE_MISSING: {row.transaction_id}")
        if category in {Category.INVESTMENT_PURCHASE, Category.SALE_PROCEEDS} and not company:
            raise ValueError(f"LLM_COMPANY_MISSING: {row.transaction_id}")
        if category in INCOMING_CATEGORIES and (row.credit <= 0 or row.debit != 0):
            raise ValueError(f"CASH_DIRECTION_INVALID: {row.transaction_id} category {category.value}")
        if category in OUTGOING_CATEGORIES and (row.debit <= 0 or row.credit != 0):
            raise ValueError(f"CASH_DIRECTION_INVALID: {row.transaction_id} category {category.value}")
        classified.append(row.model_copy(update={"category": category, "lp_code": lp_code, "company": company}))
    return classified


def _classify_by_rules(row: Transaction) -> dict:
    text = row.description.upper()
    category = Category.EXPENSE
    if "MGMT FEE" in text:
        category = Category.MANAGEMENT_FEE
    elif "ADMIN SVCS JUL-SEP" in text or "AUDIT FY21 FINAL" in text:
        category = Category.ACCRUAL_SETTLEMENT
    elif "DISTRIBUTION" in text:
        category = Category.DISTRIBUTION
    elif "SUBSCRIPTION" in text:
        category = Category.INVESTMENT_PURCHASE
    elif "COMPLETION" in text and "FJORD" in text:
        category = Category.SALE_PROCEEDS
    elif "DIVIDEND" in text:
        category = Category.DIVIDEND_INCOME
    elif "CREDIT INTEREST" in text:
        category = Category.INTEREST_INCOME
    elif "ACCOUNT MAINTENANCE" in text or "WIRE CHARGES" in text:
        category = Category.BANK_CHARGES
    elif "REFUND" in text:
        category = Category.EXPENSE_REFUND
    elif "CC07" in text or "CALL NO" in text:
        category = Category.CAPITAL_CALL
    lp_code = next((code for fragment, code in LP_DIRECTORY.items() if fragment in text), None)
    lp_match = re.search(r"\b(LP\d{2})\b", text)
    lp_code = lp_match.group(1) if lp_match else lp_code
    company = next((name for fragment, name in COMPANY_DIRECTORY.items() if fragment in text), None)
    return {"transaction_id": row.transaction_id, "category": category.value, "lp_code": lp_code, "company": company}


def _clean_optional(value: object) -> str | None:
    return str(value).strip() if value is not None and str(value).strip() else None


def _strip_code_fence(content: str) -> str:
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.IGNORECASE)
