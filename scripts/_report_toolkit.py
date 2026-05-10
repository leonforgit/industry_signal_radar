from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path


DEFAULT_TOOLKIT_ROOT = Path("~/.codex/report_toolkit")
FALLBACK_TOOLKIT_ROOTS = [
    DEFAULT_TOOLKIT_ROOT,
    Path("/opt/quant-runtime/report_toolkit"),
    Path("/srv/investment/report_toolkit"),
    Path("~/.codex/report_toolkit"),
]


def iter_toolkit_roots() -> list[Path]:
    roots: list[Path] = []
    env_root = os.environ.get("REPORT_TOOLKIT_ROOT")
    if env_root:
        roots.append(Path(env_root).expanduser())
    for candidate in FALLBACK_TOOLKIT_ROOTS:
        if candidate not in roots:
            roots.append(candidate)
    return roots


def resolve_toolkit_root() -> Path:
    for candidate in iter_toolkit_roots():
        if candidate.exists():
            return candidate
    return DEFAULT_TOOLKIT_ROOT


TOOLKIT_ROOT = resolve_toolkit_root()
BOOTSTRAP_SCRIPT = TOOLKIT_ROOT / "scripts" / "bootstrap_report_env.sh"


def ensure_report_toolkit_path() -> None:
    if importlib.util.find_spec("report_toolkit") is not None:
        return
    for candidate in iter_toolkit_roots():
        if candidate.exists() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))
            return
    project_root = Path(__file__).resolve().parents[1]
    raise ModuleNotFoundError(
        "report_toolkit is not importable. "
        "Run 'python3 scripts/bootstrap_report_env.py' "
        f"'{project_root}' or set REPORT_TOOLKIT_ROOT."
    )


ensure_report_toolkit_path()
