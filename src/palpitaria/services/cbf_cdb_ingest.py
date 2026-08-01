"""Ingestão Copa do Brasil via tabela oficial CBF (HTML).

Fonte: https://www.cbf.com.br/futebol-brasileiro/tabelas/copa-do-brasil/masculino/{season}
football-data.org não libera CDB no plano atual (403).
"""

from __future__ import annotations

import re
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from palpitaria.config import settings
from palpitaria.models import Competition, Fixture, Team
from palpitaria.services.team_names import localize_team_name

CBF_CDB_URL = (
    "https://www.cbf.com.br/futebol-brasileiro/tabelas/copa-do-brasil/masculino/{season}"
)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# CBF title → nome no banco (BSA / cadastro local)
CBF_TEAM_ALIASES: dict[str, str] = {
    "vasco da gama saf": "CR Vasco da Gama",
    "vasco da gama": "CR Vasco da Gama",
    "vasco": "CR Vasco da Gama",
    "fluminense": "Fluminense FC",
    "atlético mineiro": "CA Mineiro",
    "atletico mineiro": "CA Mineiro",
    "juventude": "Juventude",
    "santos fc": "Santos FC",
    "santos": "Santos FC",
    "remo": "Clube do Remo",
    "palmeiras": "SE Palmeiras",
    "fortaleza saf": "Fortaleza",
    "fortaleza": "Fortaleza",
    "mirassol": "Mirassol FC",
    "grêmio": "Grêmio FBPA",
    "gremio": "Grêmio FBPA",
    "chapecoense": "Chapecoense AF",
    "cruzeiro": "Cruzeiro EC",
    "internacional": "SC Internacional",
    "corinthians": "SC Corinthians Paulista",
    "athletico paranaense": "CA Paranaense",
    "athletico-pr": "CA Paranaense",
    "vitória": "EC Vitória",
    "vitoria": "EC Vitória",
    "botafogo": "Botafogo FR",
    "flamengo": "CR Flamengo",
    "bahia": "EC Bahia",
    "bragantino": "RB Bragantino",
    "red bull bragantino": "RB Bragantino",
    "são paulo": "São Paulo FC",
    "sao paulo": "São Paulo FC",
    "coritiba": "Coritiba FBC",
}


@dataclass
class CbfMatch:
    cbf_game_id: int
    home_name: str
    away_name: str
    kickoff_local: datetime
    leg: str  # Ida | Volta
    group_label: str
    stadium: str | None
    city: str | None
    state: str | None
    home_score: int | None = None
    away_score: int | None = None


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    # Ambiente Windows local às vezes sem cadeia intermediária da CBF
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def fetch_cbf_cdb_html(season: int = 2026, *, timeout: float = 45.0) -> str:
    url = CBF_CDB_URL.format(season=season)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"CBF HTTP {exc.code} em {url}") from exc
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Falha ao baixar tabela CBF: {exc}") from exc


def _parse_score_block(block: str) -> tuple[int | None, int | None]:
    # <span class="styles_gol__...">1</span> appears twice (home/away) when finished
    gols = re.findall(
        r'class="styles_gol__[^"]*"[^>]*>\s*(\d+)\s*<',
        block,
    )
    if len(gols) >= 2:
        return int(gols[0]), int(gols[1])
    return None, None


def _parse_venue(block: str) -> tuple[str | None, str | None, str | None]:
    """Extrai cidade, UF e estádio do bloco de data/local."""
    # HTML Next: 01/08/2026<!-- --> - <!-- -->17:30<br/> Rio de Janeiro <!-- --> - <!-- --> RJ<br/>Maracanã
    cleaned = re.sub(r"<!--.*?-->", "", block)
    cleaned = re.sub(r"\s+", " ", cleaned)
    m = re.search(
        r"\d{2}/\d{2}/\d{4}\s*-\s*\d{2}:\d{2}\s*<br/>\s*([^<]+?)\s*-\s*([^<]+?)\s*<br/>\s*([^<]+)",
        cleaned,
        flags=re.I,
    )
    if not m:
        m = re.search(
            r"\d{2}/\d{2}/\d{4}.{0,40}\d{2}:\d{2}.{0,20}<br/>\s*([^<]+?)\s*-\s*([^<]+?)\s*<br/>\s*([^<]+)",
            block,
            flags=re.S | re.I,
        )
    if not m:
        return None, None, None
    city = re.sub(r"\s+", " ", m.group(1)).strip(" -")
    state = re.sub(r"\s+", " ", m.group(2)).strip()
    stadium = re.sub(r"\s+", " ", m.group(3)).strip()
    # UF curta (RJ/SP) vs texto truncado
    if len(state) > 3 and " " in state:
        # ex.: "Porto Alegre Beira" — trata como estádio incompleto
        stadium = f"{state} {stadium}".strip()
        state = None
    return city or None, state or None, stadium or None


def parse_cbf_cdb_matches(html: str, *, tz_name: str | None = None) -> list[CbfMatch]:
    tz = ZoneInfo(tz_name or settings.app_timezone)
    parts = re.split(r'class="styles_card-wrapper__[^"]*"', html)[1:]
    out: list[CbfMatch] = []
    for part in parts:
        game = re.search(
            r"/futebol-brasileiro/jogos/copa-do-brasil/masculino/\d+/[^\"'/]+/(\d+)",
            part,
        )
        teams = re.findall(r'<strong title="([^"]+)">', part)
        when = re.search(r"(\d{2}/\d{2}/\d{4}).{0,60}?(\d{2}:\d{2})", part, flags=re.S)
        tag = re.search(
            r'class="styles_tag__[^"]*"[^>]*>.*?<span>([^<]+)</span>.*?<span>([^<]+)</span>',
            part,
            flags=re.S,
        )
        if not game or len(teams) < 2 or not when:
            continue
        day_s, time_s = when.group(1), when.group(2)
        local = datetime.strptime(f"{day_s} {time_s}", "%d/%m/%Y %H:%M").replace(tzinfo=tz)
        city, state, stadium = _parse_venue(part)
        hs, as_ = _parse_score_block(part)
        group_label = tag.group(1).strip() if tag else ""
        leg = tag.group(2).strip() if tag else ""
        out.append(
            CbfMatch(
                cbf_game_id=int(game.group(1)),
                home_name=teams[0].strip(),
                away_name=teams[1].strip(),
                kickoff_local=local,
                leg=leg,
                group_label=group_label,
                stadium=stadium,
                city=city,
                state=state,
                home_score=hs,
                away_score=as_,
            )
        )
    # dedupe by cbf id
    by_id = {m.cbf_game_id: m for m in out}
    return sorted(by_id.values(), key=lambda m: (m.kickoff_local, m.cbf_game_id))


def resolve_cbf_team_name(raw: str) -> str:
    key = re.sub(r"\s+", " ", raw.strip().lower())
    key = key.replace("saf", "").strip()
    if key in CBF_TEAM_ALIASES:
        return CBF_TEAM_ALIASES[key]
    # try without trailing tokens
    for alias, canon in CBF_TEAM_ALIASES.items():
        if alias in key or key in alias:
            return canon
    return localize_team_name(raw)


def _upsert_team(db: Session, display_name: str) -> Team:
    name = resolve_cbf_team_name(display_name)
    team = db.query(Team).filter(Team.name == name).one_or_none()
    if team:
        return team
    # fuzzy
    token = re.sub(r"[^a-z0-9áéíóúãõâêôç ]", "", name.lower())
    for cand in db.query(Team).all():
        cn = cand.name.lower()
        if token in cn or cn in token:
            return cand
        # last significant word
        last = name.split()[-1].lower()
        if len(last) >= 4 and last in cn:
            return cand
    # create with synthetic external id in CBF club range
    import zlib

    ext = 5_000_000 + (zlib.adler32(name.lower().encode()) % 900_000)
    team = Team(external_id=ext, name=name, short_name=name[:60])
    db.add(team)
    db.flush()
    return team


def cbf_fixture_external_id(cbf_game_id: int) -> int:
    return 6_000_000 + int(cbf_game_id)


def _stage_for_leg(leg: str, group_label: str) -> str:
    _ = group_label
    # Oitavas atuais; fases futuras ainda usam LAST_16/QUARTER etc. via grupo se necessário
    return "LAST_16"


def ingest_cdb_from_cbf(
    db: Session,
    *,
    season: int = 2026,
    log_callback: Callable[[str], None] | None = None,
    html: str | None = None,
) -> dict:
    def log(msg: str) -> None:
        if log_callback:
            log_callback(msg)

    comp = db.query(Competition).filter_by(code="CDB").one_or_none()
    if comp is None:
        raise RuntimeError("Competição CDB ausente no banco")
    if not comp.is_active:
        comp.is_active = True
        log("CDB ativada (estava inativa)")

    raw = html if html is not None else fetch_cbf_cdb_html(season)
    matches = parse_cbf_cdb_matches(raw)
    log(f"CBF CDB {season}: {len(matches)} jogo(s) na tabela oficial")

    created = updated = 0
    for match in matches:
        home = _upsert_team(db, match.home_name)
        away = _upsert_team(db, match.away_name)
        utc = match.kickoff_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
        ext = cbf_fixture_external_id(match.cbf_game_id)
        fx = db.query(Fixture).filter_by(external_id=ext).one_or_none()
        if fx is None:
            # migra seed manual anterior (mesmo confronto + horário próximo)
            candidates = (
                db.query(Fixture)
                .filter(
                    Fixture.competition_code == "CDB",
                    Fixture.home_team_id == home.id,
                    Fixture.away_team_id == away.id,
                )
                .all()
            )
            fx = None
            for cand in candidates:
                cand_utc = cand.utc_date.replace(tzinfo=None) if cand.utc_date.tzinfo else cand.utc_date
                if abs((cand_utc - utc).total_seconds()) <= 48 * 3600:
                    fx = cand
                    break

        status = "FINISHED" if match.home_score is not None else "TIMED"
        if fx is None:
            fx = Fixture(
                external_id=ext,
                competition_code="CDB",
                season=season,
                stage=_stage_for_leg(match.leg, match.group_label),
                group_name=f"{match.group_label} {match.leg}".strip() or None,
                utc_date=utc,
                status=status,
                home_team_id=home.id,
                away_team_id=away.id,
            )
            db.add(fx)
            created += 1
        else:
            fx.external_id = ext
            fx.competition_code = "CDB"
            fx.season = season
            fx.stage = _stage_for_leg(match.leg, match.group_label)
            fx.group_name = f"{match.group_label} {match.leg}".strip() or None
            fx.utc_date = utc
            if status == "FINISHED" or fx.status in ("SCHEDULED", "TIMED"):
                fx.status = status
            fx.home_team_id = home.id
            fx.away_team_id = away.id
            updated += 1

        if match.stadium:
            fx.venue_stadium = match.stadium
        if match.city:
            fx.venue_city = match.city
        if match.state:
            fx.venue_state = match.state
        if match.home_score is not None:
            fx.home_score = match.home_score
            fx.away_score = match.away_score

    db.commit()
    log(f"CDB CBF ingest: +{created} ~{updated}")
    return {"fixtures": created + updated, "created": created, "updated": updated, "parsed": len(matches)}
