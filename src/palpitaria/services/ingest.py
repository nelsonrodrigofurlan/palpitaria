from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from palpitaria.config import settings
from palpitaria.models import Fixture, Team, TeamProfile
from palpitaria.services.football_data_client import FootballDataClient
from palpitaria.services.profile_matches import build_matches_snapshot
from palpitaria.services.team_names import localize_team_name
from palpitaria.services.venues import apply_venue


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).replace(
        tzinfo=None
    )


def _upsert_team(db: Session, payload: dict) -> Team | None:
    external_id = payload.get("id")
    if not external_id:
        return None

    team = db.query(Team).filter_by(external_id=external_id).one_or_none()
    if team is None:
        team = Team(
            external_id=external_id,
            name=localize_team_name(payload.get("name", "Unknown"), external_id),
            short_name=payload.get("shortName"),
            tla=payload.get("tla"),
            crest_url=payload.get("crest"),
        )
        db.add(team)
    else:
        team.name = localize_team_name(payload.get("name", team.name), external_id)
        team.short_name = payload.get("shortName")
        team.tla = payload.get("tla")
        team.crest_url = payload.get("crest")
    return team


def _extract_score(match: dict, side: str) -> int | None:
    score = match.get("score", {})
    full_time = score.get("fullTime") or score.get("regularTime") or {}
    value = full_time.get(side)
    return int(value) if value is not None else None


def ingest_competition(
    db: Session,
    client: FootballDataClient | None = None,
    competition_code: str | None = None,
    log_callback=None,
) -> dict[str, int]:
    from palpitaria.models import Competition
    def log(msg):
        if log_callback:
            log_callback(msg)

    client = client or FootballDataClient()
    code = competition_code or settings.world_cup_code
    
    # Buscar season no banco se existir
    comp_db = db.query(Competition).filter_by(code=code).first()
    season = comp_db.season if comp_db else settings.world_cup_season

    log(f"Iniciando sincronização: {code} {season}")
    matches = client.get_competition_matches(code, season=season)
    teams_payload = client.get_competition_teams(code, season=season)

    team_count = 0
    for payload in teams_payload:
        team = _upsert_team(db, payload)
        if team:
            log(f"Time upserted: {team.name}")
        team_count += 1
    db.flush()

    fixture_count = 0
    skipped_no_date = 0
    skipped_tbd = 0
    for match in matches:
        utc_raw = match.get("utcDate")
        if not utc_raw:
            skipped_no_date += 1
            continue

        home = _upsert_team(db, match.get("homeTeam") or {})
        away = _upsert_team(db, match.get("awayTeam") or {})
        if home is None or away is None:
            skipped_tbd += 1
            continue
        db.flush()

        external_id = match["id"]
        fixture = db.query(Fixture).filter_by(external_id=external_id).one_or_none()
        if fixture is None:
            fixture = Fixture(external_id=external_id, competition_code=code, season=season)
            db.add(fixture)

        fixture.matchday = match.get("matchday")
        fixture.stage = match.get("stage")
        fixture.group_name = match.get("group")
        fixture.utc_date = _parse_utc(utc_raw)
        fixture.status = match.get("status", "SCHEDULED")
        fixture.home_team_id = home.id
        fixture.away_team_id = away.id
        fixture.home_score = _extract_score(match, "home")
        fixture.away_score = _extract_score(match, "away")
        apply_venue(fixture)
        fixture_count += 1

    log(f"Fixtures processadas: {fixture_count}")
    db.commit()
    result = {"teams": team_count, "fixtures": fixture_count}
    if skipped_no_date:
        result["skipped_no_date"] = skipped_no_date
    if skipped_tbd:
        result["skipped_tbd"] = skipped_tbd
    return result


def _match_dedup_key(match: dict) -> str:
    home = (match.get("homeTeam") or {}).get("name", "")
    away = (match.get("awayTeam") or {}).get("name", "")
    h = _extract_score(match, "home")
    a = _extract_score(match, "away")
    pair = tuple(sorted([str(home).lower().strip(), str(away).lower().strip()]))
    return f"{pair[0]}|{pair[1]}|{h}-{a}"


def _merge_match_lists(*lists: list[dict]) -> list[dict]:
    seen: set[str] = set()
    merged: list[dict] = []
    for batch in lists:
        for match in batch:
            key = _match_dedup_key(match)
            if key in seen:
                continue
            seen.add(key)
            merged.append(match)
    return merged


def _fixture_to_api_match(fixture: Fixture, home: Team, away: Team) -> dict:
    utc = fixture.utc_date
    if utc is not None and getattr(utc, "tzinfo", None) is not None:
        utc = utc.replace(tzinfo=None)
    return {
        "id": fixture.external_id,
        "utcDate": utc.isoformat() if utc else None,
        "status": fixture.status,
        "homeTeam": {"id": home.external_id, "name": home.name},
        "awayTeam": {"id": away.external_id, "name": away.name},
        "score": {"fullTime": {"home": fixture.home_score, "away": fixture.away_score}},
    }


def finished_competition_matches_for_team(
    db: Session, team_id: int, competition_code: str
) -> list[dict]:
    """FINISHED fixtures already synced for this competition (fallback when API lags)."""
    fixtures = (
        db.query(Fixture)
        .filter(Fixture.competition_code == competition_code)
        .filter(Fixture.status == "FINISHED")
        .filter((Fixture.home_team_id == team_id) | (Fixture.away_team_id == team_id))
        .order_by(Fixture.utc_date.desc())
        .all()
    )
    rows: list[dict] = []
    for fixture in fixtures:
        if fixture.home_score is None or fixture.away_score is None:
            continue
        home = db.get(Team, fixture.home_team_id)
        away = db.get(Team, fixture.away_team_id)
        if home is None or away is None:
            continue
        rows.append(_fixture_to_api_match(fixture, home, away))
    return rows


def teams_missing_profiles(db: Session, competition_code: str) -> list[Team]:
    team_ids: set[int] = set()
    for home_id, away_id in db.query(Fixture.home_team_id, Fixture.away_team_id).filter(
        Fixture.competition_code == competition_code
    ):
        team_ids.add(home_id)
        team_ids.add(away_id)
    if not team_ids:
        return []
    missing: list[Team] = []
    for team in db.query(Team).filter(Team.id.in_(team_ids)).order_by(Team.name):
        if latest_profile(db, team.id) is None:
            missing.append(team)
    return missing


def profile_updated_today(profile: TeamProfile, tz_name: str | None = None) -> bool:
    """Perfil API considerado fresco só no dia operacional (6h→6h no fuso do app)."""
    from palpitaria.services.analyzer import get_today_context

    ctx = get_today_context(tz_name)
    computed = profile.computed_at.replace(tzinfo=ZoneInfo("UTC"))
    start = ctx.start_utc.replace(tzinfo=ZoneInfo("UTC"))
    end = ctx.end_utc.replace(tzinfo=ZoneInfo("UTC"))
    return start <= computed < end


def build_team_profiles(
    db: Session,
    client: FootballDataClient | None = None,
    log_callback=None,
    *,
    competition_code: str | None = None,
    today_only: bool = True,
) -> int:
    def log(msg):
        if log_callback:
            log_callback(msg)

    client = client or FootballDataClient()
    now = datetime.utcnow()
    updated = 0
    code = competition_code or settings.world_cup_code

    if today_only:
        from palpitaria.services.analyzer import get_today_context
        from palpitaria.services.wc_profile_web import teams_playing_today

        ctx = get_today_context()
        today_teams = teams_playing_today(db, competition_code=code)
        missing_teams = teams_missing_profiles(db, code)
        by_id = {team.id: team for team in today_teams}
        for team in missing_teams:
            by_id.setdefault(team.id, team)
        teams = sorted(by_id.values(), key=lambda t: t.name)
        if not teams:
            log(f"Nenhum jogo de {code} hoje ({ctx.label}) e nenhum perfil pendente.")
            return 0
        if today_teams and missing_teams:
            log(
                f"Competição {code}: {len(today_teams)} time(s) hoje ({ctx.label}) "
                f"+ {len(missing_teams)} sem perfil válido (backfill — não gera leitura)..."
            )
        elif missing_teams:
            log(
                f"Competição {code}: {len(missing_teams)} seleção(ões) sem perfil válido "
                f"— backfill de histórico (leituras continuam só nos jogos do dia)..."
            )
        else:
            log(f"Competição {code}: {len(teams)} times com jogo hoje ({ctx.label})...")
    else:
        team_ids: set[int] = set()
        for home_id, away_id in db.query(Fixture.home_team_id, Fixture.away_team_id).filter(
            Fixture.competition_code == code
        ):
            team_ids.add(home_id)
            team_ids.add(away_id)
        teams = db.query(Team).filter(Team.id.in_(team_ids)).order_by(Team.name).all() if team_ids else []
        log(f"Competição {code}: {len(teams)} seleções no calendário...")

    api_calls = 0
    for team in teams:
        existing = latest_profile(db, team.id)
        if today_only:
            if existing and existing.matches_sampled >= 1 and profile_updated_today(existing):
                log(f"Skip {team.name} (perfil API já atualizado hoje)")
                continue
            if existing and existing.matches_sampled >= 1:
                log(f"Atualizando {team.name} (novo dia de jogo — refresh API)...")
        elif existing and existing.matches_sampled >= 1:
            log(f"Skip {team.name} (perfil já existe)")
            continue

        if api_calls > 0:
            log("Rate limit: aguardando 6.5s...")
            time.sleep(6.5)  # football-data.org free tier: 10 req/min

        log(f"Processando {team.name} ({team.external_id})...")
        api_matches: list[dict] = []
        try:
            api_matches = client.get_team_matches(team.external_id, limit=30)
            log(f"  -> API: {len(api_matches)} jogo(s) finalizado(s)")
        except Exception as e:
            log(f"  !! Erro ao buscar jogos na API: {e}")

        api_calls += 1

        db_matches = finished_competition_matches_for_team(db, team.id, code)
        matches = _merge_match_lists(api_matches, db_matches)
        if db_matches and len(db_matches) > len(api_matches):
            log(f"  -> Banco: {len(db_matches)} jogo(s) FINISHED na {code}")

        if not matches:
            log(f"  -> Sem jogos finalizados (API + banco) — passo 3 usa perfil web")
            continue

        stats = _compute_match_stats(matches, team.external_id)
        if stats["matches_sampled"] < 1:
            log(f"  -> Sem jogos finalizados válidos — passo 3 usa perfil web")
            continue

        if api_matches and db_matches:
            stats["source"] = "api+db"
        elif db_matches:
            stats["source"] = "db"
        else:
            stats["source"] = "api"
        stats["api_matches"] = len(api_matches)
        stats["db_matches"] = len(db_matches)
        stats["recent_matches"] = build_matches_snapshot(matches, team.name, team.external_id, limit=3)
        stats["calc_matches"] = build_matches_snapshot(
            matches, team.name, team.external_id, limit=max(stats["matches_sampled"], 3)
        )
        save_team_profile(db, team.id, stats, computed_at=now, preserve_insights=True)
        updated += 1
        log(f"  -> Perfil salvo: media {stats['avg_goals_scored']} gols/jogo")

    db.commit()
    log("Processamento concluído!")
    return updated


def _compute_match_stats(matches: list[dict], team_external_id: int) -> dict:
    sampled = 0
    goals_scored = 0
    goals_conceded = 0
    zero_zero = 0
    over_05 = 0
    over_15 = 0
    over_25 = 0
    wins = 0
    btts = 0

    for match in matches:
        home_id = match["homeTeam"]["id"]
        home = _extract_score(match, "home")
        away = _extract_score(match, "away")
        if home is None or away is None:
            continue

        sampled += 1
        if home_id == team_external_id:
            scored, conceded = home, away
            if home > away:
                wins += 1
        else:
            scored, conceded = away, home
            if away > home:
                wins += 1

        goals_scored += scored
        goals_conceded += conceded
        total = home + away
        if total == 0:
            zero_zero += 1
        if total >= 1:
            over_05 += 1
        if total >= 2:
            over_15 += 1
        if total >= 3:
            over_25 += 1
        if home > 0 and away > 0:
            btts += 1

    if sampled == 0:
        return {
            "matches_sampled": 0,
            "avg_goals_scored": 0.0,
            "avg_goals_conceded": 0.0,
            "zero_zero_rate": 1.0,
            "over_05_rate": 0.0,
            "over_15_rate": 0.0,
            "over_25_rate": 0.0,
            "win_rate": 0.0,
            "both_teams_score_rate": 0.0,
        }

    return {
        "matches_sampled": sampled,
        "avg_goals_scored": round(goals_scored / sampled, 3),
        "avg_goals_conceded": round(goals_conceded / sampled, 3),
        "zero_zero_rate": round(zero_zero / sampled, 3),
        "over_05_rate": round(over_05 / sampled, 3),
        "over_15_rate": round(over_15 / sampled, 3),
        "over_25_rate": round(over_25 / sampled, 3),
        "win_rate": round(wins / sampled, 3),
        "both_teams_score_rate": round(btts / sampled, 3),
    }


def save_team_profile(
    db: Session,
    team_id: int,
    stats: dict,
    *,
    computed_at: datetime | None = None,
    preserve_insights: bool = False,
) -> TeamProfile:
    """Persist stats profile; optionally copy insights from prior row."""
    insights_json = None
    if preserve_insights:
        prior = (
            db.query(TeamProfile)
            .filter_by(team_id=team_id)
            .filter(TeamProfile.insights_json.isnot(None))
            .order_by(TeamProfile.computed_at.desc())
            .first()
        )
        if prior:
            insights_json = prior.insights_json

    profile = TeamProfile(
        team_id=team_id,
        computed_at=computed_at or datetime.utcnow(),
        matches_sampled=stats["matches_sampled"],
        avg_goals_scored=stats["avg_goals_scored"],
        avg_goals_conceded=stats["avg_goals_conceded"],
        zero_zero_rate=stats["zero_zero_rate"],
        over_05_rate=stats["over_05_rate"],
        over_15_rate=stats["over_15_rate"],
        over_25_rate=stats["over_25_rate"],
        win_rate=stats["win_rate"],
        both_teams_score_rate=stats["both_teams_score_rate"],
        insights_json=insights_json,
        raw_json=json.dumps(stats, ensure_ascii=False),
    )
    db.add(profile)
    db.commit()
    return profile


def latest_profile(db: Session, team_id: int) -> TeamProfile | None:
    """Most recent profile with at least one finished match in the sample."""
    return (
        db.query(TeamProfile)
        .filter_by(team_id=team_id)
        .filter(TeamProfile.matches_sampled >= 1)
        .order_by(TeamProfile.computed_at.desc())
        .first()
    )


def localize_existing_teams(db: Session) -> int:
    """Backfill PT-BR names for teams already in the database."""
    updated = 0
    for team in db.query(Team).all():
        localized = localize_team_name(team.name, team.external_id)
        if team.name != localized:
            team.name = localized
            updated += 1
    if updated:
        db.commit()
    return updated
