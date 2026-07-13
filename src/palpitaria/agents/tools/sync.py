"""sincronizar_competicoes — BSA/BSB (+WC) via football-data / Odds fallback."""

from __future__ import annotations

from typing import Any

from palpitaria.database import SessionLocal
from palpitaria.models import Fixture
from palpitaria.services.analyzer import get_today_context
from palpitaria.services.club_profile_web import enrich_club_profiles_today
from palpitaria.services.competitions import ensure_competitions, get_competition_profile
from palpitaria.services.football_data_client import FootballDataClient, FootballDataError
from palpitaria.services.ingest import ingest_competition
from palpitaria.services.odds_ingest import ingest_competition_from_odds


def _log(msg: str) -> None:
    print(f"  [sync] {msg}", flush=True)


def _sync_one(db, client: FootballDataClient, code: str) -> dict[str, Any]:
    result: dict[str, Any] = {"code": code, "fixtures": 0, "source": None, "error": None}
    try:
        ingested = ingest_competition(db, client, competition_code=code, log_callback=_log)
        result["fixtures"] = int(ingested.get("fixtures") or 0)
        result["source"] = "football-data"
        return result
    except FootballDataError as exc:
        result["error"] = str(exc)
        if code != "BSB":
            return result
        _log(f"{code} bloqueado em football-data — Odds API")
    except Exception as exc:  # noqa: BLE001 — tool result must surface any failure
        result["error"] = str(exc)
        if code != "BSB":
            return result
        _log(f"{code} ingest falhou ({exc}) — Odds API")

    try:
        sport = get_competition_profile("BSB").odds_api_sport or "soccer_brazil_serie_b"
        ingested = ingest_competition_from_odds(
            db, "BSB", sport_key=sport, log_callback=_log
        )
        result["fixtures"] = int(ingested.get("fixtures") or 0)
        result["source"] = "odds-api"
        result["error"] = None
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)
        result["source"] = "odds-api-failed"
    return result


def _try_update_odds(db, code: str) -> str | None:
    try:
        from palpitaria.main import update_competition_odds

        update_competition_odds(db, code)
        return None
    except Exception as exc:  # noqa: BLE001
        return str(exc)


def sincronizar_competicoes(
    competicoes: list[str] | None = None,
    forcar: bool = False,
) -> dict[str, Any]:
    _ = forcar  # reserved: force refresh profiles
    codes = [c.strip().upper() for c in (competicoes or ["BSA", "BSB"]) if c]
    db = SessionLocal()
    erros: list[str] = []
    fixtures_por_comp: dict[str, int] = {}
    detalhes: list[dict[str, Any]] = []

    try:
        ensure_competitions(db, activate_brazil=True)
        client = FootballDataClient()
        for code in codes:
            if code == "WC":
                try:
                    ingested = ingest_competition(
                        db, client, competition_code="WC", log_callback=_log
                    )
                    fixtures_por_comp["WC"] = int(ingested.get("fixtures") or 0)
                    detalhes.append(
                        {"code": "WC", "fixtures": fixtures_por_comp["WC"], "source": "football-data"}
                    )
                except Exception as exc:  # noqa: BLE001
                    erros.append(f"WC: {exc}")
                    fixtures_por_comp["WC"] = 0
                continue

            row = _sync_one(db, client, code)
            fixtures_por_comp[code] = int(row.get("fixtures") or 0)
            detalhes.append(row)
            if row.get("error"):
                erros.append(f"{code}: {row['error']}")

            odds_err = _try_update_odds(db, code)
            if odds_err:
                erros.append(f"odds {code}: {odds_err}")

        ctx = get_today_context()
        hoje: dict[str, int] = {}
        for code in codes:
            n = (
                db.query(Fixture)
                .filter(
                    Fixture.competition_code == code,
                    Fixture.utc_date >= ctx.start_utc,
                    Fixture.utc_date < ctx.end_utc,
                )
                .count()
            )
            hoje[code] = n

        if "BSB" in codes:
            try:
                enrich_club_profiles_today(db, "BSB", log_callback=_log, force=True)
            except Exception as exc:  # noqa: BLE001
                erros.append(f"profiles BSB: {exc}")

        status = "ok" if not erros else ("partial" if any(fixtures_por_comp.values()) else "error")
        return {
            "status": status,
            "fixtures_por_comp": fixtures_por_comp,
            "hoje_por_comp": hoje,
            "dia_label": ctx.label,
            "detalhes": detalhes,
            "erros": erros,
        }
    finally:
        db.close()
