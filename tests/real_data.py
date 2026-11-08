"""Real-sample helpers for the Read tests. Only the tuning deals are ever used (D-02)."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from typing import Optional

import pytest

ROOT = Path(__file__).resolve().parents[1]


def sample_root() -> Optional[Path]:
    """The participant data pack (env PRISM_SAMPLE_DATA, else ROOT/data/participant), or None."""
    env = os.environ.get("PRISM_SAMPLE_DATA")
    p = Path(env) if env else ROOT / "data" / "participant"
    return p if (p / "shared_folder").is_dir() else None


def holdout_names() -> list[str]:
    data = json.loads((ROOT / "eval" / "holdout.json").read_text(encoding="utf-8"))
    return sorted(data["holdout"])


def tuning_folders(root: Path) -> list[str]:
    """Sorted deal folder names minus the holdout names."""
    hold = set(holdout_names())
    return sorted(p.name for p in (root / "shared_folder").iterdir()
                  if p.is_dir() and not p.name.startswith(".") and p.name not in hold)


def load_score_split():
    """Import scripts/score_split.py as a module (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location("score_split", ROOT / "scripts" / "score_split.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


requires_sample = pytest.mark.skipif(sample_root() is None, reason="sample data not present")
