"""Diagnose why today's fixtures are excluded."""
from palpitaria.database import SessionLocal
from palpitaria.services.analyzer import analyze_upcoming, count_teams_with_profiles, get_today_context
from palpitaria.services.ingest import latest_profile
from palpitaria.config import settings
from palpitaria.models import Fixture, Team

db = SessionLocal()
ready, total = count_teams_with_profiles(db)
ctx = get_today_context()
print("=== CONFIG THRESHOLDS ===")
print(f"min_combined_avg_goals: {settings.min_combined_avg_goals}")
print(f"max_zero_zero_rate: {settings.max_zero_zero_rate}")
print(f"min_over_05_historical_rate: {settings.min_over_05_historical_rate}")
print(f"min_both_score_rate: {settings.min_both_score_rate}")
print(f"Profiles ready: {ready}/{total}")
print(f"Today: {ctx.label} ({ctx.timezone})")
print()

analyses = analyze_upcoming(db, for_today_only=True)
print(f"Games today: {len(analyses)}")
for a in analyses:
    f = db.query(Fixture).filter_by(id=a.fixture_id).first()
    hp = latest_profile(db, f.home_team_id)
    ap = latest_profile(db, f.away_team_id)
    print(f"\n--- {a.home_name} x {a.away_name} ---")
    print(f"  excluded: {a.excluded}, score: {a.goal_potential_score}")
    for r in a.exclusion_reasons:
        print(f"  REASON: {r}")
    for label, p in [("home", hp), ("away", ap)]:
        if p is None:
            print(f"  {label}: NO PROFILE")
        else:
            print(
                f"  {label}: sampled={p.matches_sampled} "
                f"scored={p.avg_goals_scored:.2f} conceded={p.avg_goals_conceded:.2f} "
                f"over05={p.over_05_rate:.2f} zero0={p.zero_zero_rate:.2f} btts={p.both_teams_score_rate:.2f}"
            )
    if a.criteria:
        for c in a.criteria:
            mark = "OK" if c.passed else "FAIL"
            print(f"  [{mark}] {c.name}: {c.value} (need {c.threshold})")

# List teams with profiles vs without
print("\n=== TEAMS WITHOUT VALID PROFILE ===")
for team in db.query(Team).order_by(Team.name).all():
    p = latest_profile(db, team.id)
    if p is None or p.matches_sampled < 1:
        print(f"  - {team.name} (profile={'missing' if p is None else '0 matches'})")

db.close()
