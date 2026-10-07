"""Regenerates frontend/src/data/exerciseCatalog.json from the installed garminconnect
package's own exercise catalog (garminconnect.exercises._RAW) -- the same 1,527-exercise/
47-category list `create_strength_exercise_step` accepts, used by the hiit/strength_training
exercise picker (docs/ARCHITECTURE.md). Re-run this whenever the
garminconnect dependency is upgraded and its own exercise list has changed (that package's own
module docstring says to regenerate its _RAW list with its own scripts/generate_exercises.py --
this script instead just re-exports whatever is currently installed for the frontend to bundle).

Usage: uv run python scripts/generate_exercise_catalog.py
"""

from __future__ import annotations

import json
from pathlib import Path

from garminconnect.exercises import _RAW

OUT_PATH = (
    Path(__file__).resolve().parent.parent / "frontend" / "src" / "data" / "exerciseCatalog.json"
)


def _category_label(category: str) -> str:
    return category.replace("_", " ").title()


def main() -> None:
    entries = [
        {
            "name": name,
            "category": category,
            "categoryLabel": _category_label(category),
            "exercise": exercise,
        }
        for name, category, exercise in _RAW
    ]
    entries.sort(key=lambda e: (e["categoryLabel"], e["name"]))
    OUT_PATH.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    num_categories = len({e["category"] for e in entries})
    print(f"Wrote {len(entries)} exercises across {num_categories} categories to {OUT_PATH}")


if __name__ == "__main__":
    main()
