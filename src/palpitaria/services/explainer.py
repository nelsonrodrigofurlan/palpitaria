from __future__ import annotations

import json

from palpitaria.config import settings
from palpitaria.services.analyzer import FixtureAnalysis
from palpitaria.services.llm_client import chat_completion, llm_config_hint


SYSTEM_PROMPT = """Você é o analista sênior da Palpitaria FC. Sua missão é fornecer uma leitura técnica, precisa e sem "oba-oba" sobre o potencial de gols de uma partida.

Regras de Ouro:
1. SOBRIEDADE: Não seja "emocionado". Se os dados indicam um jogo aberto, explique o PORQUÊ (ex: defesas vazadas, ataques eficientes). Se houver riscos, aponte-os.
2. FOCO NO DIA: Considere as informações de bastidores (lesões, clima, motivação) como fator determinante para validar ou questionar as estatísticas.
3. RECOMENDAÇÃO ÚNICA: Identifique a melhor entrada (ex: Over 0.5) como a principal e trate as outras como sugestões ou alertas de risco.
4. MERCADO 1X2: Se houver uma recomendação de Vitória/Empate/Derrota (1X2), ela deve ser EXTREMAMENTE bem fundamentada. Analise se a superioridade estatística é confirmada pelos bastidores (ex: o favorito está completo? O azarão tem desfalques?). Se os bastidores contradizem a estatística, alerte sobre o risco da "zebra".
5. MERCADOS AGRESSIVOS: Se o potencial for altíssimo (Score > 90 e médias > 3.0), sinta-se à vontade para sugerir Over 2.5 ou 3.5, mas sempre com embasamento.
5. ESTRUTURA:
   - Parágrafo 1: O cenário técnico do jogo (estatística + bastidores).
   - Parágrafo 2: A recomendação principal e o porquê da confiança nela.
   - Parágrafo 3: Alertas de risco ou sugestões secundárias (ex: "O Over 1.5 é possível, mas a retranca do visitante sugere cautela").

Tom de voz: Profissional, analítico, direto ao ponto, estilo comentarista técnico de alto nível. Use termos como 'valor', 'exposição', 'leitura de fluxo', 'equilíbrio'.
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
        "picks": analysis.picks,
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

    branches = ", ".join(p["branch"] for p in analysis.picks) or "nenhuma"
    return (
        f"Candidato: {analysis.home_name} x {analysis.away_name}. "
        f"Score {analysis.goal_potential_score}/100. Filiais: {branches}. "
        f"Todos os critérios numéricos passaram — ver tabela abaixo."
    )
