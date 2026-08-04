"""Registry de competições — regras por liga (BSA/BSB/WC/CDB)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CompetitionProfile:
    code: str
    name: str
    season_default: int
    min_sample_games: int
    home_advantage_goals: float  # boost λ mandante
    data_strategy: str  # "api_first" | "hybrid_web"
    odds_api_sport: str | None
    form_window: int  # últimos N jogos com peso maior
    knockout_default: bool
    edge_min_homologate: float  # edge mínimo vs odd para homologar


PROFILES: dict[str, CompetitionProfile] = {
    "BSA": CompetitionProfile(
        code="BSA",
        name="Brasileirão Série A",
        season_default=2026,
        min_sample_games=5,
        home_advantage_goals=0.28,
        data_strategy="api_first",
        odds_api_sport="soccer_brazil_campeonato",
        form_window=5,
        knockout_default=False,
        edge_min_homologate=0.04,
    ),
    "BSB": CompetitionProfile(
        code="BSB",
        name="Brasileirão Série B",
        season_default=2026,
        min_sample_games=5,
        home_advantage_goals=0.32,  # mando ainda mais forte na B
        data_strategy="api_first",
        odds_api_sport="soccer_brazil_serie_b",
        form_window=5,
        knockout_default=False,
        edge_min_homologate=0.05,
    ),
    "WC": CompetitionProfile(
        code="WC",
        name="Copa do Mundo",
        season_default=2026,
        min_sample_games=1,
        home_advantage_goals=0.08,
        data_strategy="hybrid_web",
        odds_api_sport="soccer_fifa_world_cup",
        form_window=3,
        knockout_default=False,
        edge_min_homologate=0.03,
    ),
    "CDB": CompetitionProfile(
        code="CDB",
        name="Copa do Brasil",
        season_default=2026,
        min_sample_games=2,
        home_advantage_goals=0.22,
        data_strategy="cbf_official",  # tabela CBF (football-data 403 no plano atual)
        odds_api_sport=None,
        form_window=5,
        knockout_default=True,
        edge_min_homologate=0.04,
    ),
}


def get_competition_profile(code: str | None) -> CompetitionProfile:
    if not code:
        return PROFILES["BSA"]
    return PROFILES.get(code.upper(), PROFILES.get("BSA", next(iter(PROFILES.values()))))


def active_competition_codes(db) -> list[str]:
    """Códigos das competições ativas (ordem estável)."""
    from palpitaria.models import Competition

    rows = (
        db.query(Competition)
        .filter(Competition.is_active.is_(True))
        .order_by(Competition.code)
        .all()
    )
    return [r.code for r in rows]


def resolve_competition_codes(
    db,
    competition_code: str | None = None,
    competition_codes: list[str] | None = None,
) -> list[str]:
    """
    Resolve filtro de campeonato.
    - lista explícita → essa lista
    - um código → [código]
    - None → todos os ativos (visão geral do dia)
    """
    if competition_codes is not None:
        return [c.strip().upper() for c in competition_codes if c and str(c).strip()]
    if competition_code:
        code = competition_code.strip().upper()
        if code == "ALL":
            return active_competition_codes(db)
        return [code]
    return active_competition_codes(db)


def ensure_competitions(db, *, activate_bsa: bool = True, activate_bsb: bool = False) -> list[str]:
    """Garante linhas BSA/BSB/CDB/WC na tabela competitions.

    Estado padrão pós-Copa: BSA ativa, CDB ativa; BSB e WC ficam como o admin
    deixar (Copa encerrada; Série B fora do escopo por custo de token) — não
    forçamos reativação delas aqui, só de BSA.
    """
    from palpitaria.models import Competition

    touched: list[str] = []
    for code, profile in PROFILES.items():
        row = db.query(Competition).filter_by(code=code).one_or_none()
        if row is None:
            if code == "BSA":
                active = activate_bsa
            elif code == "BSB":
                active = activate_bsb
            elif code == "CDB":
                active = True
            else:  # WC
                active = False
            db.add(
                Competition(
                    code=code,
                    name=profile.name,
                    season=profile.season_default,
                    is_active=active,
                )
            )
            touched.append(f"+{code}")
        else:
            row.name = profile.name
            if row.season < profile.season_default:
                row.season = profile.season_default
            if code == "BSA" and activate_bsa:
                row.is_active = True
            touched.append(f"~{code}")
    db.commit()
    return touched
