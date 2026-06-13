from __future__ import annotations

import json

from palpitaria.config import settings
from palpitaria.services.analyzer import FixtureAnalysis
from palpitaria.services.llm_client import chat_completion, llm_config_hint


SYSTEM_PROMPT = """Você é o analista sênior da Palpitaria FC. Sua missão é fornecer uma leitura técnica, precisa e sem "oba-oba" sobre o potencial de gols de uma partida.

Regras de Ouro:
1. SOBRIEDADE: Não seja "emocionado". Se os dados indicam um jogo aberto, explique o PORQUÊ (ex: defesas vazadas, ataques eficientes). Se houver riscos, aponte-os.
2. FOCO NO DIA: Considere as informações de bastidores (lesões, clima, motivação) e as condições de jogo (Árbitro, Clima, Gramado) como fatores determinantes para validar ou questionar as estatísticas.
3. RECOMENDAÇÃO ÚNICA: Identifique a melhor entrada (ex: Over 0.5, Vitória, etc.) como a principal e fundamente-a com base em todos os dados.
4. ARBITRAGEM E CLIMA: Analise se o árbitro é do tipo que "deixa o jogo rolar" ou se é rigoroso (o que pode travar o jogo ou gerar expulsões). Veja se o clima (chuva, calor extremo) favorece ou atrapalha o fluxo de gols.
5. ESTRUTURA:
   - Parágrafo 1: O cenário técnico e o momento das equipes (bastidores).
   - Parágrafo 2: A recomendação principal fundamentada (estatística + condições de jogo).
   - Parágrafo 3: Alertas de risco específicos (ex: "O árbitro rigoroso pode gerar muitas interrupções, dificultando o Over 1.5").

Tom de voz: Profissional, analítico, direto ao ponto. Use termos como 'valor', 'exposição', 'leitura de fluxo', 'equilíbrio'.
Máximo 3 parágrafos. Não invente dados."""


def explain_analysis(analysis: FixtureAnalysis) -> str:
    payload = {
        "home": analysis.home_name,
        "away": analysis.away_name,
        "stage": analysis.stage,
        "group": analysis.group_name,
        "excluded": analysis.excluded,
        "exclusion_reasons": analysis.exclusion_reasons,
        "goal_potential_score": analysis.goal_potential_score,
        "criteria": [
            {
                "name": c.name,
                "value": c.value,
                "threshold": c.threshold,
                "passed": c.passed,
                "detail": c.detail,
            }
            for c in analysis.criteria
        ],
        "home_insights": analysis.home_insights,
        "away_insights": analysis.away_insights,
        "best_pick": analysis.best_pick,
        "match_context": analysis.match_context,
    }

    if not settings.has_llm:
        return _fallback_explanation(analysis)

    try:
        user_content = f"Dados do jogo:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
        text = chat_completion(SYSTEM_PROMPT, user_content)
        return text if text else _fallback_explanation(analysis)
    except Exception as exc:
        hint = llm_config_hint(exc)
        return f"{_fallback_explanation(analysis)}\n\n(LLM indisponível: {hint})"


def _fallback_explanation(analysis: FixtureAnalysis) -> str:
    if analysis.excluded:
        reasons = "; ".join(analysis.exclusion_reasons) or "critérios não atendidos"
        return (
            f"Descartado: {analysis.home_name} x {analysis.away_name}. "
            f"Filtro anti-zero-gols: {reasons}. "
            f"Score de potencial: {analysis.goal_potential_score}/100."
        )

    pick = analysis.best_pick
    if pick:
        return (
            f"Candidato: {analysis.home_name} x {analysis.away_name}. "
            f"Score {analysis.goal_potential_score}/100. "
            f"Recomendação: {pick.get('market', '—')} ({pick.get('verdict', '—')}). "
            f"{pick.get('reason', '')}"
        )

    return (
        f"Candidato: {analysis.home_name} x {analysis.away_name}. "
        f"Score {analysis.goal_potential_score}/100. "
        f"Todos os critérios numéricos passaram — ver tabela abaixo."
    )
