"""Validação cruzada dos contadores do Histórico IA."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from palpitaria.database import SessionLocal
from palpitaria.models import AiRecommendation, Fixture, FixtureReport, Team
from palpitaria.services.ai_tracker import (
    compute_split_stats,
    ensure_ia_history_from_reports,
    evaluate_market,
    filter_recommendations_by_month,
    prune_discarded_pending_recommendations,
    rows_for_scope,
)
from palpitaria.services.ledger import current_period


def main() -> None:
    db = SessionLocal()
    cy, cm = current_period()
    comp = "WC"

    prune_discarded_pending_recommendations(db, comp)
    ensure_ia_history_from_reports(db, comp, cy, cm)
    all_recs = db.query(AiRecommendation).filter(AiRecommendation.competition_code == comp).all()
    filtered = filter_recommendations_by_month(all_recs, cy, cm)
    split = compute_split_stats(filtered)
    hom = rows_for_scope(filtered, homologated=True)
    alt = rows_for_scope(filtered, homologated=False)

    print("=" * 60)
    print("CHECKLIST CONTADORES — Histórico IA (WC, mês atual)")
    print("=" * 60)

    # 1. Manual recount vs compute_split_stats
    def count(rows):
        h = sum(1 for r in rows if r["outcome"] == "HIT")
        m = sum(1 for r in rows if r["outcome"] == "MISS")
        p = sum(1 for r in rows if r["outcome"] == "PENDING")
        res = h + m
        pct = round(h / res * 100) if res else None
        return h, m, p, pct

    hom_h, hom_m, hom_p, hom_pct = count(hom)
    alt_h, alt_m, alt_p, alt_pct = count(alt)
    sh = split["homologated"]
    sa = split["alternate"]

    ok = True
    for label, manual, stats in [
        ("Homologadas", (hom_h, hom_m, hom_p, hom_pct), sh),
        ("Alternativas", (alt_h, alt_m, alt_p, alt_pct), sa),
    ]:
        mh, mm, mp, mpct = manual
        match = (
            mh == stats["hits"]
            and mm == stats["misses"]
            and mp == stats["pending"]
            and mpct == stats["hit_rate_pct"]
        )
        status = "OK" if match else "ERRO"
        if not match:
            ok = False
        print(f"\n[{status}] {label}")
        print(f"  Manual:  {mh}H / {mm}M / {mp}P = {mpct}%")
        print(f"  Stats:   {stats['hits']}H / {stats['misses']}M / {stats['pending']}P = {stats['hit_rate_pct']}%")

    # 2. Every finished game in reports should appear in historico
    print("\n[CHECK] Reports de junho com pick -> presente no historico?")
    missing_from_hist = []
    mismatch_pick = []
    mismatch_outcome = []
    for rep in db.query(FixtureReport).filter(FixtureReport.best_pick_json.isnot(None)).all():
        f = db.get(Fixture, rep.fixture_id)
        if not f or f.competition_code != comp:
            continue
        if not rep.analyzed_at or rep.analyzed_at.month != cm or rep.analyzed_at.year != cy:
            continue
        pick = json.loads(rep.best_pick_json or "{}")
        if not pick.get("market"):
            continue
        h = db.get(Team, f.home_team_id)
        a = db.get(Team, f.away_team_id)
        rec = db.query(AiRecommendation).filter_by(fixture_id=f.id).first()
        if not rec:
            missing_from_hist.append(f"{h.name} x {a.name}")
            continue
        if rec.market != pick["market"]:
            mismatch_pick.append(f"{h.name} x {a.name}: report={pick['market']} db={rec.market}")
        if f.status == "FINISHED" and f.home_score is not None:
            expected = evaluate_market(
                rec.market,
                home_name=h.name,
                away_name=a.name,
                home_score=f.home_score,
                away_score=f.away_score,
            )
            if rec.outcome != expected:
                mismatch_outcome.append(
                    f"{h.name} x {a.name}: db={rec.outcome} esperado={expected} ({f.home_score}x{f.away_score})"
                )

    if missing_from_hist:
        ok = False
        print("  FALTANDO:", *missing_from_hist, sep="\n    ")
    else:
        print("  Todos presentes: OK")

    if mismatch_pick:
        ok = False
        print("  PICK DIFERENTE DO REPORT:")
        for m in mismatch_pick:
            print(f"    {m}")
    else:
        print("  Picks batem com report: OK")

    if mismatch_outcome:
        ok = False
        print("  OUTCOME ERRADO:")
        for m in mismatch_outcome:
            print(f"    {m}")
    else:
        print("  Outcomes corretos: OK")

    # 3. Homologadas list
    print("\n--- HOMOLOGADAS (detalhe) ---")
    for r in sorted(hom, key=lambda x: x["analyzed_at"]):
        print(f"  {r['analyzed_at'].date()} | {r['match_label']} | {r['market']} | {r['score']} | {r['outcome']}")

    # 4. Key games
    print("\n--- JOGOS-CHAVE ---")
    keys = [
        ("Espanha", "Cabo Verde", "LAY CORRECT SCORE: 0-0", "MISS", "0 x 0"),
        ("Holanda", "Suécia", None, None, None),
        ("Alemanha", "Costa do Marfim", None, None, None),
        ("Equador", "Cura", None, None, None),
    ]
    all_rows = hom + alt
    for home_kw, away_kw, exp_mkt, exp_out, exp_score in keys:
        found = [
            r for r in all_rows
            if home_kw in r["match_label"] and away_kw in r["match_label"]
        ]
        if not found:
            print(f"  {home_kw} x {away_kw}: AUSENTE")
            ok = False
            continue
        r = found[0]
        line = f"  {r['match_label']} | {r['market']} | {r['score']} | {r['outcome']}"
        if exp_mkt and r["market"] != exp_mkt:
            line += f"  [esperado market={exp_mkt}]"
            ok = False
        if exp_out and r["outcome"] != exp_out:
            line += f"  [esperado outcome={exp_out}]"
            ok = False
        if exp_score and r["score"] != exp_score:
            line += f"  [esperado score={exp_score}]"
            ok = False
        print(line)

    print("\n" + ("RESULTADO GERAL: OK" if ok else "RESULTADO GERAL: PROBLEMAS ENCONTRADOS"))
    db.close()


if __name__ == "__main__":
    main()
