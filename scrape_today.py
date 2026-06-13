#!/usr/bin/env python3
"""Roda o scraper para os jogos de hoje (bastidores + contexto de jogo).

Pipeline:
  1. Números da API já devem estar no banco (passo 2 do app).
  2. Este script coleta bastidores das seleções e contexto (clima/árbitro/gramado).
  3. Depois rode "3. Gerar Leituras de Hoje" no app — ou use --full para tudo de uma vez.

Uso:
  python scrape_today.py              # só scrap (passo 2 do pipeline)
  python scrape_today.py --full       # scrap + recomendação LLM (passo 2 + 3)
  python scrape_today.py --team Brazil  # bastidores de uma seleção só
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from palpitaria.config import settings
from palpitaria.database import SessionLocal
from palpitaria.models import Fixture, Team
from palpitaria.services.analyzer import (
    analyze_upcoming,
    default_match_context,
    persist_analysis,
)
from palpitaria.services.explainer import explain_analysis
from palpitaria.services.scraper import enrich_fixture_analysis, refresh_team_insights


def scrape_today_teams(db, full: bool = False) -> None:
    if not settings.has_llm:
        print("ERRO: configure OPENAI_API_KEY no .env (OpenRouter sk-or-... funciona).")
        sys.exit(1)

    analyses = analyze_upcoming(db, limit=50, for_today_only=True)
    if not analyses:
        print("Nenhum jogo hoje. Sincronize o calendário primeiro (passo 1).")
        return

    print(f"Pipeline para {len(analyses)} jogo(s) de hoje...\n")
    for analysis in analyses:
        fixture = db.query(Fixture).filter_by(id=analysis.fixture_id).one()
        print(f"=== {analysis.home_name} x {analysis.away_name} ===")
        print(f"[1/3] Números API — score {analysis.goal_potential_score}, excluído={analysis.excluded}")

        home, away, ctx = enrich_fixture_analysis(
            db,
            fixture_id=analysis.fixture_id,
            home_team_id=fixture.home_team_id,
            away_team_id=fixture.away_team_id,
            home_name=analysis.home_name,
            away_name=analysis.away_name,
            excluded=analysis.excluded,
            home_insights=analysis.home_insights,
            away_insights=analysis.away_insights,
            log_callback=lambda msg: print(msg),
        )
        analysis.home_insights = home
        analysis.away_insights = away
        analysis.match_context = ctx or default_match_context()

        if ctx:
            print(f"  Clima: {ctx.get('weather')}")
            print(f"  Árbitro: {ctx.get('referee')}")
            print(f"  Gramado: {ctx.get('pitch')}")

        if full:
            print("[3/3] Recomendação LLM...")
            explanation = explain_analysis(analysis)
            analysis.llm_explanation = explanation
            persist_analysis(db, analysis, explanation)
            if analysis.best_pick:
                print(f"  Pick: {analysis.best_pick.get('market')} ({analysis.best_pick.get('verdict')})")
        print()

    if full:
        print("Concluído — leituras salvas no banco. Atualize a home no navegador.")
    else:
        print("Scrap concluído. Agora clique em '3. Gerar Leituras de Hoje' no app (ou rode com --full).")


def scrape_single_team(db, team_name: str) -> None:
    if not settings.has_llm:
        print("ERRO: configure OPENAI_API_KEY no .env.")
        sys.exit(1)

    team = db.query(Team).filter(Team.name.ilike(team_name)).first()
    if not team:
        print(f"Seleção '{team_name}' não encontrada. Rode a sincronização primeiro.")
        sys.exit(1)

    print(f"Coletando bastidores de {team.name}...")
    insights = refresh_team_insights(db, team.id, team.name)
    if insights:
        print(f"OK — {insights.get('backstage_info', '')[:200]}")
    else:
        print("Falha na coleta (busca web ou LLM).")


def main() -> None:
    parser = argparse.ArgumentParser(description="Scraper Palpitaria FC — bastidores e contexto de jogo")
    parser.add_argument("--full", action="store_true", help="Scrap + recomendação LLM (passos 2 e 3)")
    parser.add_argument("--team", help="Coletar bastidores de uma seleção específica")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.team:
            scrape_single_team(db, args.team)
        else:
            scrape_today_teams(db, full=args.full)
    finally:
        db.close()


if __name__ == "__main__":
    main()
