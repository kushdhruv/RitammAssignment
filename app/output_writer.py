"""Atomic JSON and JSONL outputs with safe Decimal conversion."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
import json
from pathlib import Path
import shutil
from typing import Any, Iterable
from uuid import uuid4


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, default=_json_default, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("".join(json.dumps(row, default=_json_default, allow_nan=False) + "\n" for row in rows), encoding="utf-8")
    temporary.replace(path)


def publish_directory(staging_directory: Path, output_directory: Path) -> None:
    """Publish a complete output run atomically within one filesystem."""
    if not staging_directory.is_dir():
        raise ValueError("OUTPUT_STAGING_MISSING")
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    if output_directory.exists() and not output_directory.is_dir():
        raise ValueError("OUTPUT_TARGET_NOT_DIRECTORY")
    backup: Path | None = None
    if output_directory.exists():
        backup = output_directory.with_name(f".{output_directory.name}.previous-{uuid4().hex}")
        output_directory.replace(backup)
    try:
        staging_directory.replace(output_directory)
    except Exception:
        if backup is not None and backup.exists():
            backup.replace(output_directory)
        raise
    if backup is not None:
        shutil.rmtree(backup)


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"Cannot serialize {type(value).__name__}")
