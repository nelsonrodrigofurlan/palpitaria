"""Bootstrap fixtures/teams from Odds API when football-data bloqueia a liga (ex.: BSB free tier)."""

from __future__ import annotations

import zlib
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from palpitaria.models import Competition, Fixture, Team
from palpitaria.services.odds_service import extract_betfair_odds, fetch_odds_api_data
from palpitaria.services.team_names import localize_team_name
from palpitaria.services.venues import apply_venue


def _stable_id(prefix: int, key: str) -> int:
    digest = zlib.adler32(key.encode("utf-8")) & 0xFFFFFFFF
    return prefix + (digest % 900_000)


def odds_team_external_id(name: str) -> int:
    return _stable_id(9_000_000, name.strip().lower())


def odds_fixture_external_id(odds_game_id: str) -> int:
    return _stable_id(8_000_000, odds_game_id)


def _upsert_odds_team(db: Session, name: str) -> Team:
    display = localize_team_name(name)
    ext = odds_team_external_id(display)
    team = db.query(Team).filter_by(external_id=ext).one_or_none()
    if team is None:
        # Também tenta achar por nome (time já veio da BSA)
        team = db.query(Team).filter(Team.name.ilike(display)).one_or_none()
    if team is None:
        team = Team(external_id=ext, name=display, short_name=display[:60])
        db.add(team)
        db.flush()
    else:
        team.name = display
    return team


def _parse_commence(raw: str | None) -> datetime | None:
    if not raw:
        return None
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc).replace(
        tzinfo=None
    )


def ingest_competition_from_odds(
    db: Session,
    competition_code: str,
    *,
    sport_key: str,
    log_callback=None,
) -> dict[str, int]:
    """Cria/atualiza fixtures a partir do cache Odds API (e atualiza odds_json)."""

    def log(msg: str) -> None:
        if log_callback:
            log_callback(msg)

    raw = fetch_odds_api_data(sport=sport_key)
    if isinstance(raw, dict) and raw.get("error"):
        raise RuntimeError(f"Odds API: {raw['error']}")
    if not isinstance(raw, list):
        raise RuntimeError("Odds API: resposta inesperada")

    odds_list = extract_betfair_odds(raw)
    # Se Betfair não veio, ainda assim cria fixtures com os jogos da lista crua
    games = odds_list
    if not games:
        log("Sem Betfair Ex no payload — usando jogos crus da Odds API")
        games = []
        for game in raw:
            home = localize_team_name(game.get("home_team") or "")
            away = localize_team_name(game.get("away_team") or "")
            if not home or not away:
                continue
            games.append(
                {
                    "id": game.get("id"),
                    "home_team": home,
                    "away_team": away,
                    "commence_time": game.get("commence_time"),
                    "betfair_ex": None,
                }
            )

    comp = db.query(Competition).filter_by(code=competition_code).one_or_none()
    if comp is None:
        raise RuntimeError(f"Competição {competition_code} não existe no banco")
    import json

    comp.odds_json = json.dumps(odds_list or games, ensure_ascii=False)
    season = comp.season

    fixtures = 0
    teams = 0
    for game in games:
        odds_id = str(game.get("id") or f"{game['home_team']}-{game['away_team']}-{game.get('commence_time')}")
        utc = _parse_commence(game.get("commence_time"))
        if not utc:
            continue
        home = _upsert_odds_team(db, game["home_team"])
        away = _upsert_odds_team(db, game["away_team"])
        teams += 2
        db.flush()

        ext = odds_fixture_external_id(odds_id)
        fixture = db.query(Fixture).filter_by(external_id=ext).one_or_none()
        if fixture is None:
            fixture = Fixture(external_id=ext, competition_code=competition_code, season=season)
            db.add(fixture)
        fixture.competition_code = competition_code
        fixture.season = season
        fixture.utc_date = utc
        fixture.status = "TIMED"
        fixture.stage = "REGULAR_SEASON"
        fixture.home_team_id = home.id
        fixture.away_team_id = away.id
        apply_venue(fixture)
        fixtures += 1
        log(f"  Odds fixture: {home.name} x {away.name} @ {utc.isoformat()}Z")

    db.commit()
    log(f"Odds bootstrap {competition_code}: {fixtures} jogos")
    return {"teams": teams, "fixtures": fixtures, "odds_games": len(odds_list or games)}
