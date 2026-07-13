"""Garante competições BSA/BSB ativas (e opcionalmente desativa WC)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from palpitaria.database import SessionLocal
from palpitaria.services.competitions import ensure_competitions


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--deactivate-wc",
        action="store_true",
        help="Marca WC como inativa (mantém dados históricos)",
    )
    args = parser.parse_args()
    db = SessionLocal()
    try:
        touched = ensure_competitions(db, activate_brazil=True, deactivate_wc=args.deactivate_wc)
        print("Competitions:", ", ".join(touched))
        from palpitaria.models import Competition

        for c in db.query(Competition).order_by(Competition.code):
            print(f"  {c.code}: {c.name} season={c.season} active={c.is_active}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
