"""Reavalia BSB de hoje com o portao de fundamento (sem palpite publico)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from palpitaria.database import SessionLocal
from palpitaria.services.analyzer import analyze_upcoming, persist_analysis


def main() -> None:
    db = SessionLocal()
    try:
        analyses = analyze_upcoming(db, limit=50, for_today_only=True, competition_code="BSB")
        print(f"BSB hoje: {len(analyses)} jogos")
        for a in analyses:
            persist_analysis(db, a, None, competition_code="BSB")
            pick = (a.best_pick or {}).get("market") if a.best_pick else None
            print(f"  {a.home_name} x {a.away_name}")
            print(f"    excluded={a.excluded} pick={pick}")
            for r in (a.exclusion_reasons or [])[:3]:
                print(f"    - {r}")
        print("Reports salvos (sem narrativa LLM — sem pick).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
