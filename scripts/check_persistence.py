"""Quick persistence audit for user questions."""
import json
from datetime import timedelta

from palpitaria.database import SessionLocal
from palpitaria.models import Fixture, FixtureReport, Team, TeamProfile
from palpitaria.services.analyzer import count_teams_with_profiles, get_today_context
from palpitaria.services.ingest import latest_profile

db = SessionLocal()
ready, total = count_teams_with_profiles(db)
reports = db.query(FixtureReport).count()
profiles = db.query(TeamProfile).filter(TeamProfile.matches_sampled >= 1).count()
insights = db.query(TeamProfile).filter(TeamProfile.insights_json.isnot(None)).count()

ctx = get_today_context()
yesterday_start = ctx.start_utc - timedelta(days=1)
yesterday_end = ctx.start_utc

print("=== RESUMO BANCO ===")
print(f"Seleções: {total}, perfis válidos: {ready}, linhas TeamProfile c/ jogos: {profiles}")
print(f"Perfis c/ bastidores: {insights}")
print(f"FixtureReports (leituras): {reports}")
print(f"Fixtures: {db.query(Fixture).count()}")

print("\n=== JOGOS ONTEM ===")
y_fix = (
    db.query(Fixture)
    .filter(Fixture.utc_date >= yesterday_start, Fixture.utc_date < yesterday_end)
    .order_by(Fixture.utc_date)
    .all()
)
for f in y_fix:
    ht = db.query(Team).get(f.home_team_id)
    at = db.query(Team).get(f.away_team_id)
    r = db.query(FixtureReport).filter_by(fixture_id=f.id).first()
    score = f"{f.home_score}-{f.away_score}" if f.home_score is not None else "—"
    pick = "—"
    if r and r.best_pick_json:
        pick = json.loads(r.best_pick_json).get("market", "—")
    print(
        f"  {ht.name} x {at.name} | {f.status} {score} | "
        f"leitura: {'SIM' if r else 'NAO'} | pick: {pick}"
    )

print("\n=== JOGOS HOJE ===")
t_fix = (
    db.query(Fixture)
    .filter(Fixture.utc_date >= ctx.start_utc, Fixture.utc_date < ctx.end_utc)
    .order_by(Fixture.utc_date)
    .all()
)
for f in t_fix:
    ht = db.query(Team).get(f.home_team_id)
    at = db.query(Team).get(f.away_team_id)
    r = db.query(FixtureReport).filter_by(fixture_id=f.id).first()
    hp = latest_profile(db, f.home_team_id)
    ap = latest_profile(db, f.away_team_id)
    print(
        f"  {ht.name} x {at.name} | report: {'SIM' if r else 'NAO'} | "
        f"perfis: {hp.matches_sampled if hp else 0}/{ap.matches_sampled if ap else 0} jogos"
    )

print("\n=== SELECOES COM PERFIL ===")
count = 0
for t in db.query(Team).order_by(Team.name).all():
    p = latest_profile(db, t.id)
    if not p:
        continue
    count += 1
    raw = json.loads(p.raw_json or "{}")
    print(
        f"  {t.name}: {p.matches_sampled} jogos, fonte={raw.get('source', '?')}, "
        f"insights={'sim' if p.insights_json else 'nao'}"
    )
print(f"  (total {count} seleções com perfil numérico)")

db.close()
