"""Auditoria do Histórico IA — compara banco, reports e recálculo live."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from palpitaria.database import SessionLocal
from palpitaria.models import AiRecommendation, Fixture, FixtureReport, Team
from palpitaria.services.ai_tracker import (
    compute_split_stats,
    evaluate_market,
    filter_recommendations_by_month,
    rows_for_scope,
)
from palpitaria.services.analyzer import analyze_fixture
from palpitaria.services.ledger import current_period


def main() -> None:
    db = SessionLocal()
    cy, cm = current_period()

    print("=" * 60)
    print("AUDITORIA HISTÓRICO IA — Copa do Mundo")
    print("=" * 60)

    # 1. Spain games
    print("\n[1] JOGOS DA ESPANHA")
    for f in db.query(Fixture).filter(Fixture.competition_code == "WC").order_by(Fixture.utc_date):
        h = db.get(Team, f.home_team_id)
        a = db.get(Team, f.away_team_id)
        if not h or not a:
            continue
        if "Espan" not in (h.name or "") and "Espan" not in (a.name or ""):
            continue
        score = f"{f.home_score}-{f.away_score}" if f.home_score is not None else "?"
        rec = db.query(AiRecommendation).filter_by(fixture_id=f.id).first()
        rep = db.query(FixtureReport).filter_by(fixture_id=f.id).first()
        rep_pick = None
        if rep and rep.best_pick_json:
            rep_pick = json.loads(rep.best_pick_json).get("market")
        cur = analyze_fixture(db, f)
        live = cur.best_pick.get("market") if cur.best_pick else None
        print(f"  {f.utc_date.date()} | {h.name} x {a.name} | {score}")
        print(f"    DB ai_rec:     {rec.market if rec else 'AUSENTE'} ({rec.outcome if rec else '-'})")
        print(f"    Report pick:   {rep_pick or 'AUSENTE'}")
        print(f"    Live recalc:   {live or 'DESCARTADO'}")

    # 2. All 0-0 games
    print("\n[2] JOGOS 0-0 FINALIZADOS")
    for f in db.query(Fixture).filter(
        Fixture.competition_code == "WC",
        Fixture.status == "FINISHED",
        Fixture.home_score == 0,
        Fixture.away_score == 0,
    ).order_by(Fixture.utc_date):
        h = db.get(Team, f.home_team_id)
        a = db.get(Team, f.away_team_id)
        rec = db.query(AiRecommendation).filter_by(fixture_id=f.id).first()
        rep = db.query(FixtureReport).filter_by(fixture_id=f.id).first()
        rep_pick = json.loads(rep.best_pick_json).get("market") if rep and rep.best_pick_json else None
        cur = analyze_fixture(db, f)
        live = cur.best_pick.get("market") if cur.best_pick else None
        in_hist = "SIM" if rec else "NAO"
        print(f"  {h.name} x {a.name} | rec_db={rec.market if rec else '-'} | report={rep_pick} | live={live or 'descartado'}")

    # 3. Reports with pick but no ai_recommendation
    print("\n[3] REPORTS COM PICK MAS SEM AI_RECOMMENDATION")
    missing = 0
    for rep in db.query(FixtureReport).filter(FixtureReport.best_pick_json.isnot(None)).all():
        if not rep.best_pick_json:
            continue
        pick = json.loads(rep.best_pick_json)
        if not pick.get("market"):
            continue
        rec = db.query(AiRecommendation).filter_by(fixture_id=rep.fixture_id).first()
        if rec:
            continue
        f = db.get(Fixture, rep.fixture_id)
        if not f or f.competition_code != "WC":
            continue
        h = db.get(Team, f.home_team_id)
        a = db.get(Team, f.away_team_id)
        missing += 1
        print(f"  {h.name} x {a.name} | report={pick.get('market')} | analyzed={rep.analyzed_at}")

    if missing == 0:
        print("  (nenhum)")

    # 4. AI rec deleted by sync but report still has pick (historical truth)
    print("\n[4] REPORTS HISTÓRICOS (pick salvo na análise original)")
    june_reports = []
    for rep in db.query(FixtureReport).filter(FixtureReport.best_pick_json.isnot(None)).all():
        f = db.get(Fixture, rep.fixture_id)
        if not f or f.competition_code != "WC":
            continue
        if not rep.analyzed_at or rep.analyzed_at.month != 6:
            continue
        pick = json.loads(rep.best_pick_json or "{}")
        if not pick.get("market"):
            continue
        h = db.get(Team, f.home_team_id)
        a = db.get(Team, f.away_team_id)
        outcome = "PENDING"
        if f.status == "FINISHED" and f.home_score is not None:
            outcome = evaluate_market(
                pick["market"],
                home_name=h.name,
                away_name=a.name,
                home_score=f.home_score,
                away_score=f.away_score,
            )
        june_reports.append((rep.analyzed_at, h.name, a.name, pick["market"], rep.excluded, outcome, f.home_score, f.away_score))

    for row in sorted(june_reports):
        dt, hn, an, mkt, excl, out, hs, aws = row
        tipo = "ALT" if excl else "HOM"
        sc = f"{hs}x{aws}" if hs is not None else "?"
        print(f"  {dt.date()} | {hn} x {an} | {mkt} | {tipo} | {out} | {sc}")

    # 5. Current historico page output (snapshot, junho/WC)
    print("\n[5] O QUE A PÁGINA MOSTRA HOJE (snapshot gravado, junho/WC)")
    from palpitaria.services.ai_tracker import (
        compute_split_stats,
        ensure_ia_history_from_reports,
        rows_for_scope,
    )

    ensure_ia_history_from_reports(db, "WC", cy, cm)
    all_recs = db.query(AiRecommendation).filter(AiRecommendation.competition_code == "WC").all()
    filtered = filter_recommendations_by_month(all_recs, cy, cm)
    split = compute_split_stats(filtered)
    hom = rows_for_scope(filtered, homologated=True)
    alt = rows_for_scope(filtered, homologated=False)
    print(f"  Homologadas: {split['homologated']['hits']}H / {split['homologated']['misses']}M / {split['homologated']['pending']}P = {split['homologated']['hit_rate_pct']}%")
    print(f"  Alternativas: {split['alternate']['hits']}H / {split['alternate']['misses']}M / {split['alternate']['pending']}P = {split['alternate']['hit_rate_pct']}%")
    print(f"  Total linhas hom: {len(hom)}, alt: {len(alt)}")
    print("\n  --- Alternativas ---")
    for r in alt:
        print(f"    {r['analyzed_at'].date()} | {r['match_label']} | {r['market']} | {r['score']} | {r['outcome']}")
    spain = [r for r in alt if "Espan" in r["match_label"] and "Cabo" in r["match_label"]]
    print(f"\n  Espanha x Cabo Verde presente: {'SIM' if spain else 'NAO'}")

    # 6. Spain 0-0 specifically - search by score
    print("\n[6] BUSCA: ESPANHA + 0-0 (primeira rodada)")
    for rep in db.query(FixtureReport).all():
        f = db.get(Fixture, rep.fixture_id)
        if not f:
            continue
        h = db.get(Team, f.home_team_id)
        a = db.get(Team, f.away_team_id)
        if not h or not a:
            continue
        is_spain = "Espan" in h.name or "Espan" in a.name
        is_00 = f.home_score == 0 and f.away_score == 0
        if is_spain and is_00:
            pick = json.loads(rep.best_pick_json).get("market") if rep.best_pick_json else None
            rec = db.query(AiRecommendation).filter_by(fixture_id=f.id).first()
            print(f"  ENCONTRADO: {h.name} x {a.name} | report={pick} | ai_rec={rec.market if rec else 'AUSENTE'}")

    db.close()


if __name__ == "__main__":
    main()
