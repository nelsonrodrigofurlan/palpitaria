"""Garante linhas BSA/BSB/CDB/WC no banco. Padrão pós-Copa: BSA + CDB ativas."""

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
        "--activate-bsb",
        action="store_true",
        help="Reativa BSB (desativada por padrão desde 2026-08 para economizar token)",
    )
    parser.add_argument(
        "--deactivate-wc",
        action="store_true",
        help="Marca WC como inativa (mantém dados históricos)",
    )
    args = parser.parse_args()
    db = SessionLocal()
    try:
        touched = ensure_competitions(db, activate_bsa=True, activate_bsb=args.activate_bsb)
        if args.deactivate_wc:
            from palpitaria.models import Competition

            wc = db.query(Competition).filter_by(code="WC").one_or_none()
            if wc:
                wc.is_active = False
                db.commit()
        print("Competitions:", ", ".join(touched))
        from palpitaria.models import Competition

        for c in db.query(Competition).order_by(Competition.code):
            print(f"  {c.code}: {c.name} season={c.season} active={c.is_active}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
