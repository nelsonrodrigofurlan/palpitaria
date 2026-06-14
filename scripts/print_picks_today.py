"""Resumo das recomendações de hoje para apostas."""
import json
from palpitaria.database import SessionLocal
from palpitaria.models import Fixture, FixtureReport, Team
from palpitaria.services.analyzer import analyze_upcoming, get_today_context

db = SessionLocal()
ctx = get_today_context()
analyses = analyze_upcoming(db, for_today_only=True)

print(f"=== RECOMENDAÇÕES {ctx.label} ===\n")
candidates = []
for a in analyses:
    r = db.query(FixtureReport).filter_by(fixture_id=a.fixture_id).first()
    pick = json.loads(r.best_pick_json) if r and r.best_pick_json else a.best_pick
    expl = (r.llm_explanation or a.llm_explanation or "")[:300] if r else ""
    line = f"{a.home_name} x {a.away_name} | score {a.goal_potential_score}"
    if a.excluded:
        print(f"DESCARTADO — {line}")
        if a.exclusion_reasons:
            print(f"  Motivo: {'; '.join(a.exclusion_reasons[:2])}")
        if pick:
            print(f"  Sugestão numérica: {pick.get('market')} — {pick.get('reason', '')[:120]}")
    else:
        candidates.append(a)
        print(f"CANDIDATO — {line}")
        if pick:
            print(f"  MERCADO: {pick.get('market')} ({pick.get('verdict')})")
            print(f"  Motivo: {pick.get('reason', '')}")
            if pick.get("web_factor"):
                print(f"  Web: {pick.get('web_factor')}")
    if expl:
        print(f"  Leitura: {expl}...")
    print()

print(f"Total: {len(candidates)} candidato(s) de {len(analyses)} jogos")
db.close()
