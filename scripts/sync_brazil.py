"""Sync BSA + BSB; BSB cai para Odds API se football-data der 403."""

from __future__ import annotations

import json
import sys
from datetime import timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from palpitaria.database import SessionLocal
from palpitaria.main import update_competition_odds
from palpitaria.models import Fixture, Team
from palpitaria.services.analyzer import analyze_upcoming, get_today_context
from palpitaria.services.club_profile_web import enrich_club_profiles_today
from palpitaria.services.competitions import ensure_competitions, get_competition_profile
from palpitaria.services.football_data_client import FootballDataClient, FootballDataError
from palpitaria.services.ingest import ingest_competition
from palpitaria.services.odds_ingest import ingest_competition_from_odds


def log(msg: str) -> None:
    print(msg)


def main() -> None:
    db = SessionLocal()
    try:
        ensure_competitions(db, activate_brazil=True)
        client = FootballDataClient()

        # --- BSA (API) ---
        print("=" * 60)
        print("SYNC BSA")
        try:
            print(ingest_competition(db, client, competition_code="BSA", log_callback=log))
        except Exception as exc:
            print(f"INGEST ERROR BSA: {exc}")
        try:
            update_competition_odds(db, "BSA")
            log("ODDS BSA ok")
        except Exception as exc:
            print(f"ODDS ERROR BSA: {exc}")

        # --- BSB (API ou Odds fallback) ---
        print("=" * 60)
        print("SYNC BSB")
        bsb_ok = False
        try:
            print(ingest_competition(db, client, competition_code="BSB", log_callback=log))
            bsb_ok = True
        except FootballDataError as exc:
            log(f"football-data BSB bloqueado ({exc}) — bootstrap via Odds API")
        except Exception as exc:
            log(f"INGEST ERROR BSB: {exc} — tentando Odds API")

        if not bsb_ok:
            sport = get_competition_profile("BSB").odds_api_sport or "soccer_brazil_serie_b"
            print(
                ingest_competition_from_odds(
                    db, "BSB", sport_key=sport, log_callback=log
                )
            )

        ctx = get_today_context()
        print("=" * 60)
        print(f"HOJE {ctx.label} ({ctx.timezone})")
        for code in ("BSA", "BSB"):
            rows = (
                db.query(Fixture)
                .filter(
                    Fixture.competition_code == code,
                    Fixture.utc_date >= ctx.start_utc,
                    Fixture.utc_date < ctx.end_utc,
                )
                .order_by(Fixture.utc_date)
                .all()
            )
            print(f"{code} hoje: {len(rows)}")
            for fixture in rows:
                home = db.query(Team).filter_by(id=fixture.home_team_id).one_or_none()
                away = db.query(Team).filter_by(id=fixture.away_team_id).one_or_none()
                local = fixture.utc_date.replace(tzinfo=timezone.utc).astimezone(
                    ZoneInfo("America/Sao_Paulo")
                )
                print(
                    f"  {local.strftime('%H:%M')} "
                    f"{home.name if home else '?'} x {away.name if away else '?'} "
                    f"[{fixture.status}]"
                )

        print("=" * 60)
        print("PROFILES BSB (web clube — so se houver fundamento)...")
        n = enrich_club_profiles_today(db, "BSB", log_callback=log, force=True)
        print(f"profiles web BSB: {n}")
        print(
            "Nota: Nao seedamos perfil via odds 1X2 para homologacao "
            "(responsabilidade: sem historico real = sem palpite)."
        )

        print("=" * 60)
        analyses = analyze_upcoming(db, limit=50, for_today_only=True, competition_code="BSB")
        print(f"ANALYSES BSB: {len(analyses)}")
        for analysis in analyses:
            pick = (analysis.best_pick or {}).get("market") if analysis.best_pick else None
            print(
                f"  {analysis.home_name} x {analysis.away_name} "
                f"score={analysis.goal_potential_score} excluded={analysis.excluded} pick={pick}"
            )
            if analysis.prediction:
                print(
                    f"    P(O1.5)={analysis.prediction.get('p_over_15')} "
                    f"P(O2.5)={analysis.prediction.get('p_over_25')} "
                    f"lam={analysis.prediction.get('lam_home')}/"
                    f"{analysis.prediction.get('lam_away')}"
                )
    finally:
        db.close()


if __name__ == "__main__":
    main()
