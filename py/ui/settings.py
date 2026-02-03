from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List


APP_DIR = Path.home() / ".xel_toolkit"
CONFIG_PATH = APP_DIR / "config.json"


@dataclass
class AppConfig:
    recent_workspaces: List[str]
    inputmd_mode: str  # "overwrite" | "append"
    confirm_inputmd_overwrite: bool


def _load_raw() -> Dict[str, Any]:
    try:
        if not CONFIG_PATH.exists():
            return {}
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def load_config() -> AppConfig:
    raw = _load_raw()
    return AppConfig(
        recent_workspaces=list(raw.get("recent_workspaces", []) or []),
        inputmd_mode=str(raw.get("inputmd_mode", "overwrite") or "overwrite"),
        confirm_inputmd_overwrite=bool(raw.get("confirm_inputmd_overwrite", True)),
    )


def save_config(cfg: AppConfig) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(
            {
                "recent_workspaces": cfg.recent_workspaces,
                "inputmd_mode": cfg.inputmd_mode,
                "confirm_inputmd_overwrite": cfg.confirm_inputmd_overwrite,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def push_recent(lst: List[str], value: str, limit: int = 10) -> List[str]:
    value = str(value)
    out = [value] + [x for x in lst if x != value]
    return out[:limit]
