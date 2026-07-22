"""Motor de predição — Poisson independente; modelo decide, LLM só narra."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

from palpitaria.services.competitions import CompetitionProfile, get_competition_profile
from palpitaria.services.knockout_climate import is_knockout_stage


def _poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam**k) / math.factorial(k)


def _poisson_cdf_ge(threshold: int, lam: float, *, max_goals: int = 10) -> float:
    """P(X >= threshold) via 1 - P(X <= threshold-1)."""
    if threshold <= 0:
        return 1.0
    cdf = sum(_poisson_pmf(k, lam) for k in range(0, threshold))
    return max(0.0, min(1.0, 1.0 - cdf))


def _score_matrix(
    lam_home: float,
    lam_away: float,
    *,
    max_goals: int = 8,
) -> list[list[float]]:
    return [
        [_poisson_pmf(i, lam_home) * _poisson_pmf(j, lam_away) for j in range(max_goals + 1)]
        for i in range(max_goals + 1)
    ]


@dataclass
class MatchPrediction:
    lam_home: float
    lam_away: float
    p_over_05: float
    p_over_15: float
    p_over_25: float
    p_btts: float
    p_home: float
    p_draw: float
    p_away: float
    p_zero_zero: float
    competition_code: str
    is_knockout: bool
    best_market: str | None
    best_prob: float
    verdict: str  # STRONG | CANDIDATE | SKIP
    reason: str
    scope: str  # goals | alternate | skip
    scenarios: dict[str, float]  # pessimista / realista / otimista (gols totais esperados)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def as_best_pick(self) -> dict[str, Any] | None:
        if self.scope == "skip" or not self.best_market:
            return None
        return {
            "market": self.best_market,
            "verdict": self.verdict,
            "reason": self.reason,
            "scope": self.scope,
            "model_prob": round(self.best_prob, 4),
            "lambdas": {"home": self.lam_home, "away": self.lam_away},
            "probs": {
                "over_05": self.p_over_05,
                "over_15": self.p_over_15,
                "over_25": self.p_over_25,
                "btts": self.p_btts,
                "1x2": {"home": self.p_home, "draw": self.p_draw, "away": self.p_away},
            },
            "scenarios": self.scenarios,
        }


def _clamp_lam(value: float, *, lo: float = 0.35, hi: float = 3.2) -> float:
    return max(lo, min(hi, value))


def estimate_lambdas(
    *,
    home_scored: float,
    home_conceded: float,
    away_scored: float,
    away_conceded: float,
    profile: CompetitionProfile,
    is_knockout: bool = False,
) -> tuple[float, float]:
    """λ ≈ média ataque próprio + defesa adversária, com mando e ajuste mata-mata."""
    attack_home = (home_scored + away_conceded) / 2.0
    attack_away = (away_scored + home_conceded) / 2.0
    lam_home = attack_home + profile.home_advantage_goals
    lam_away = attack_away
    if is_knockout:
        # Eliminatória: comprime gols esperados (~12%)
        lam_home *= 0.88
        lam_away *= 0.88
    return _clamp_lam(lam_home), _clamp_lam(lam_away)


def predict_match(
    *,
    home_scored: float,
    home_conceded: float,
    away_scored: float,
    away_conceded: float,
    competition_code: str | None,
    stage: str | None = None,
    home_name: str = "Mandante",
    away_name: str = "Visitante",
    league_avg_goals: float = 2.5,
) -> MatchPrediction:
    profile = get_competition_profile(competition_code)
    knockout = is_knockout_stage(stage) or profile.knockout_default
    lam_h, lam_a = estimate_lambdas(
        home_scored=home_scored,
        home_conceded=home_conceded,
        away_scored=away_scored,
        away_conceded=away_conceded,
        profile=profile,
        is_knockout=knockout,
    )
    matrix = _score_matrix(lam_h, lam_a)
    p_home = p_draw = p_away = p_btts = p_zz = 0.0
    for i, row in enumerate(matrix):
        for j, p in enumerate(row):
            if i > j:
                p_home += p
            elif i == j:
                p_draw += p
            else:
                p_away += p
            if i > 0 and j > 0:
                p_btts += p
            if i == 0 and j == 0:
                p_zz += p

    total_lam = lam_h + lam_a
    p_over_05 = _poisson_cdf_ge(1, total_lam)
    p_over_15 = _poisson_cdf_ge(2, total_lam)
    p_over_25 = _poisson_cdf_ge(3, total_lam)

    scenarios = {
        "pessimista": round(max(0.5, total_lam * 0.7), 2),
        "realista": round(total_lam, 2),
        "otimista": round(total_lam * 1.3, 2),
    }

    # Decisão de mercado (gols primeiro) — sem LLM
    best_market, best_prob, verdict, scope, reason = _select_market(
        p_over_05=p_over_05,
        p_over_15=p_over_15,
        p_over_25=p_over_25,
        p_home=p_home,
        p_away=p_away,
        p_zz=p_zz,
        knockout=knockout,
        profile=profile,
        home_name=home_name,
        away_name=away_name,
        total_lam=total_lam,
        league_avg=league_avg_goals,
    )

    return MatchPrediction(
        lam_home=round(lam_h, 3),
        lam_away=round(lam_a, 3),
        p_over_05=round(p_over_05, 4),
        p_over_15=round(p_over_15, 4),
        p_over_25=round(p_over_25, 4),
        p_btts=round(p_btts, 4),
        p_home=round(p_home, 4),
        p_draw=round(p_draw, 4),
        p_away=round(p_away, 4),
        p_zero_zero=round(p_zz, 4),
        competition_code=profile.code,
        is_knockout=knockout,
        best_market=best_market,
        best_prob=best_prob,
        verdict=verdict,
        reason=reason,
        scope=scope,
        scenarios=scenarios,
    )


def _select_market(
    *,
    p_over_05: float,
    p_over_15: float,
    p_over_25: float,
    p_home: float,
    p_away: float,
    p_zz: float,
    knockout: bool,
    profile: CompetitionProfile,
    home_name: str,
    away_name: str,
    total_lam: float,
    league_avg: float,
) -> tuple[str | None, float, str, str, str]:
    # Mata-mata: Over 2.5 quase nunca homologado
    over25_floor = 0.72 if knockout else 0.62
    over15_floor = 0.68 if knockout else 0.62
    over05_floor = 0.88

    if p_over_25 >= over25_floor and total_lam >= league_avg + 0.6 and not knockout:
        return (
            "OVER 2.5 GOALS",
            p_over_25,
            "STRONG" if p_over_25 >= 0.70 else "CANDIDATE",
            "goals",
            f"Modelo Poisson: P(Over 2.5)={p_over_25:.0%} com λ total {total_lam:.2f} ({profile.code}).",
        )

    if p_over_15 >= over15_floor:
        return (
            "OVER 1.5 GOALS",
            p_over_15,
            "STRONG" if p_over_15 >= 0.75 else "CANDIDATE",
            "goals",
            (
                f"Modelo Poisson: P(Over 1.5)={p_over_15:.0%} (λ={total_lam:.2f}). "
                + ("Mata-mata: linha 1.5 preferida ao 2.5. " if knockout else "")
                + f"Perfil {profile.code}."
            ),
        )

    if p_over_05 >= over05_floor and p_zz <= 0.10:
        return (
            "OVER 0.5 GOALS",
            p_over_05,
            "CANDIDATE",
            "goals",
            f"Anti-zero: P(Over 0.5)={p_over_05:.0%}, P(0-0)={p_zz:.0%}.",
        )

    # Alternativa só com favorito VALIDADO (nunca LAY 0-0 — equivale a Over 0.5)
    fav_p, fav_name = (p_home, home_name) if p_home >= p_away else (p_away, away_name)
    if fav_p >= 0.58:
        # Favorito esmagador → handicap -1; favorito claro → ML
        if fav_p >= 0.68 and total_lam >= 2.2:
            return (
                f"HANDICAP ASIÁTICO: {fav_name} -1",
                fav_p,
                "STRONG" if fav_p >= 0.72 else "CANDIDATE",
                "alternate",
                f"Sem Over homologado — favorito forte P={fav_p:.0%}; linha -1 ({profile.code}).",
            )
        return (
            f"VITÓRIA: {fav_name}",
            fav_p,
            "CANDIDATE",
            "alternate",
            f"Sem base sólida de Over — favorito validado P={fav_p:.0%} ({profile.code}).",
        )

    return (
        None,
        0.0,
        "SKIP",
        "skip",
        f"Descarte total: sem Over nem favorito validado ({profile.code}, λ={total_lam:.2f}).",
    )


def implied_prob_from_odds(odds: float | None) -> float | None:
    if odds is None or odds <= 1.0:
        return None
    return 1.0 / odds


def edge(model_prob: float, market_odds: float | None) -> float | None:
    implied = implied_prob_from_odds(market_odds)
    if implied is None:
        return None
    return round(model_prob - implied, 4)
