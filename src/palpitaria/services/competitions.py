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

    # Filtro anti-zero-gols (analyzer.py) — limiares do gate de exclusão.
    # Calibrados por competição porque a distribuição de gols de seleções
    # (Copa) e clubes brasileiros (BSA/BSB/CDB) não é a mesma.
    min_combined_avg_goals: float
    strong_combined_avg_goals: float
    max_zero_zero_rate: float
    strong_max_zero_zero_rate: float
    min_both_score_rate: float
    strong_both_score_rate: float
    min_over_05_historical_rate: float
    strong_over_05_historical_rate: float
    min_offense_goals: float
    strong_offense_goals: float


# Limiares originais do MVP Copa do Mundo (seleções) — nunca recalibrados
# para clubes; times nacionais têm variância de gols bem diferente de ligas
# de pontos corridos. Mantidos como estavam (WC desativada, mas preservados
# como referência histórica).
_WC_THRESHOLDS = dict(
    min_combined_avg_goals=2.0,
    strong_combined_avg_goals=3.5,
    max_zero_zero_rate=0.12,
    strong_max_zero_zero_rate=0.05,
    min_both_score_rate=0.55,
    strong_both_score_rate=0.70,
    min_over_05_historical_rate=0.88,
    strong_over_05_historical_rate=0.95,
    min_offense_goals=0.8,
    strong_offense_goals=1.5,
)

# Recalibrados em 2026-08-04 contra dados reais dos 20 clubes da Série A
# (perfis com >=5 jogos amostrados, matchups par-a-par). Os antigos valores
# (herdados do MVP Copa) eram, na prática, inertes para clube: combined_avg
# min=2.0 passava em 100% dos confrontos reais e strong=3.5 não era
# alcançado em nenhum (máximo observado ~3.07); strong_both_score_rate=0.70
# também nunca era alcançado (p90 real = 0.657). Os demais limiares já
# batiam razoavelmente com a distribuição real e foram mantidos.
_BSA_THRESHOLDS = dict(
    min_combined_avg_goals=2.3,  # era 2.0 (não-filtro); p10 real = 2.41
    strong_combined_avg_goals=2.75,  # era 3.5 (inalcançável); p75 real = 2.73
    max_zero_zero_rate=0.12,  # ok — exclui só a cauda pior (~p80+)
    strong_max_zero_zero_rate=0.05,  # ok — alcançável por ~25-40% dos confrontos
    min_both_score_rate=0.55,  # ok — perto da mediana real (0.57)
    strong_both_score_rate=0.62,  # era 0.70 (inalcançável); p90 real = 0.66
    min_over_05_historical_rate=0.88,  # ok — entre p10 (0.85) e p25 (0.90)
    strong_over_05_historical_rate=0.95,  # ok — bate com p75 real
    min_offense_goals=0.95,  # era 0.8 (quase não-filtro); pior time real = 0.895
    strong_offense_goals=1.5,  # ok — só os ataques realmente fortes passam
)

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
        **_BSA_THRESHOLDS,
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
        # Desativada (2026-08) — herda calibração BSA (mesmo tipo de liga
        # brasileira de clubes) até haver amostra própria pra validar.
        **_BSA_THRESHOLDS,
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
        **_WC_THRESHOLDS,
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
        # Clubes majoritariamente compartilhados com BSA/BSB; amostra CDB
        # própria é pequena demais (mata-mata) pra calibrar isolado.
        **_BSA_THRESHOLDS,
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
