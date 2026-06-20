"""Seed example designs so a fresh install has parts to explore.

Usage:
    backend/.venv/bin/python -m scripts.seed
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal, init_db  # noqa: E402
from app.services.design_service import create_design  # noqa: E402

SEED_PROMPTS = [
    "Mounting bracket 90 mm wide, 50 mm deep, 6 mm thick with two M6 counterbored "
    "holes for socket-head screws and a gusset, rounded corners.",
    "Wall bracket 100 mm wide, 50 mm deep, 6 mm thick, two M8 countersunk holes.",
    "Electronics enclosure 100 mm wide, 60 mm deep, 40 mm tall with 2.5 mm walls and "
    "M3 corner screws.",
    "Project box 120 x 80 x 50 mm with 3 mm walls and a screw-down lid.",
    "Drill jig plate 100 mm by 60 mm, 6 mm thick, with 5 mm guide holes spaced 20 mm "
    "and a registration lip.",
    "Adapter plate 100 mm square, 6 mm thick, with a 30 mm center bore and four M6 "
    "clearance holes.",
    "CNC aluminum adapter plate 120 mm square, 8 mm thick, 25 mm center bore.",
]


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        for prompt in SEED_PROMPTS:
            design = create_design(db, prompt, project_id=None, name=None)
            if design.spec_json is None:
                status = "needs clarification"
            else:
                status = f"{design.object_type} ({len(design.exports)} exports)"
            print(f"  • {design.id[:8]}  {status}")
    finally:
        db.close()
    print("Seed complete.")


if __name__ == "__main__":
    main()
