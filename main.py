"""CLI entry point required by the take-home brief."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import logging

from app.orchestrator import run_close


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip())


def main() -> int:
    _configure_logging()
    try:
        _load_dotenv(Path(".env"))
        project_root = Path(__file__).resolve().parent
        default_pack_dir = project_root / "pack"
        if not default_pack_dir.is_dir():
            default_pack_dir = project_root.parent / "pack"
        pack_dir = Path(sys.argv[1] if len(sys.argv) > 1 else os.getenv("PACK_DIR", str(default_pack_dir))).resolve()
        classifier_mode = "deterministic" if os.getenv("DEMO_CLASSIFIER") == "1" else "llm"
        result = run_close(pack_dir, Path("./output").resolve(), classifier_mode=classifier_mode,
                           agent_a_port=int(os.getenv("AGENT_A_PORT", "8001")), agent_b_port=int(os.getenv("AGENT_B_PORT", "8002")))
    except ValueError as error:
        logging.getLogger("gpf_nav_close").error("Close rejected: %s", error)
        return 2
    except Exception:
        logging.getLogger("gpf_nav_close").exception("Quarter-close execution failed")
        return 1
    print(f"Q4 NAV: {result['nav_q4']}")
    return 0


def _configure_logging() -> None:
    requested_level = os.getenv("LOG_LEVEL", "WARNING").upper()
    level = getattr(logging, requested_level, logging.WARNING)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")


if __name__ == "__main__":
    raise SystemExit(main())
