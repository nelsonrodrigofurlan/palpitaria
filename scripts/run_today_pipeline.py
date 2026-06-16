#!/usr/bin/env python3
"""Pipeline completo de hoje: sync → perfis API → leituras (web stalking + LLM)."""
from __future__ import annotations

import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from palpitaria.config import settings
from palpitaria.database import SessionLocal
from palpitaria.models import Fixture
from palpitaria.services.analyzer import (
    analyze_upcoming,
    count_teams_with_profiles,
    default_match_context,
    get_today_context,
    persist_analysis,
)
from palpitaria.services.explainer import explain_analysis, refine_best_pick
from palpitaria.services.football_data_client import FootballDataClient
from palpitaria.services.ingest import build_team_profiles, ingest_competition, localize_existing_teams
from palpitaria.services.scraper import enrich_fixture_analysis
from palpitaria.services.wc_profile_web import enrich_today_team_profiles


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> None:
    if not settings.has_football_token:
        print("ERRO: FOOTBALL_DATA_TOKEN ausente no .env")
        sys.exit(1)
    if not settings.has_llm:
        print("ERRO: OPENAI_API_KEY ausente no .env")
        sys.exit(1)

    today = get_today_context()
    db = SessionLocal()
    try:
        log(f"=== Pipeline Palpitaria FC — {today.label} ===\n")

        log("[1/3] Sincronizando jogos (API)...")
        client = FootballDataClient()
        ingest = ingest_competition(db, client, log_callback=log)
        renamed = localize_existing_teams(db)
        log(f"  -> {ingest.get('fixtures', 0)} fixtures, {renamed} nomes PT-BR\n")

        log("[2/3] Perfis API (seleções de hoje)...")
        profiles = build_team_profiles(
            db, client, log_callback=log, competition_code=settings.world_cup_code, today_only=True
        )
        ready, total = count_teams_with_profiles(db)
        log(f"  -> {profiles} atualizados, {ready}/{total} prontas no banco\n")

        analyses = analyze_upcoming(db, limit=50, for_today_only=True)
        if not analyses:
            log("Nenhum jogo hoje. Encerrando.")
            return

        log(f"[3/3] Leituras — {len(analyses)} jogo(s) (stalking core-6 + LLM)...")
        web_n = enrich_today_team_profiles(db, log_callback=log, force_refresh=True)
        log(f"  -> {web_n} perfil(is) híbrido(s)\n")

        analyses = analyze_upcoming(db, limit=50, for_today_only=True)
        candidates = 0

        for analysis in analyses:
            fixture = db.query(Fixture).filter_by(id=analysis.fixture_id).one()
            log(f"\n--- {analysis.home_name} x {analysis.away_name} (score {analysis.goal_potential_score}) ---")

            home_i, away_i, ctx = enrich_fixture_analysis(
                db,
                fixture_id=analysis.fixture_id,
                external_id=fixture.external_id,
                home_team_id=fixture.home_team_id,
                away_team_id=fixture.away_team_id,
                home_name=analysis.home_name,
                away_name=analysis.away_name,
                excluded=analysis.excluded,
                home_insights=analysis.home_insights,
                away_insights=analysis.away_insights,
                log_callback=log,
            )
            analysis.home_insights = home_i
            analysis.away_insights = away_i
            analysis.match_context = ctx or default_match_context()
            analysis.best_pick = refine_best_pick(analysis)
            explanation = explain_analysis(analysis)
            analysis.llm_explanation = explanation
            persist_analysis(db, analysis, explanation)

            if analysis.excluded:
                log(f"  DESCARTADO: {'; '.join(analysis.exclusion_reasons)}")
            else:
                candidates += 1
                pick = analysis.best_pick or {}
                log(f"  CANDIDATO: {pick.get('market')} ({pick.get('verdict')})")
                if pick.get("web_factor"):
                    log(f"  Web: {pick.get('web_factor')}")

        log(f"\n=== Concluído: {len(analyses)} leituras, {candidates} candidatos ===")
        log("Atualize http://127.0.0.1:8000/ no navegador.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
