from __future__ import annotations

import json

from palpitaria.config import settings
from palpitaria.services.analyzer import FixtureAnalysis
from palpitaria.services.llm_client import chat_completion, llm_config_hint
from palpitaria.services.scraper import _parse_json_from_llm

SYSTEM_PROMPT = """Você é o analista sênior da Palpitaria FC. Sua missão é fornecer uma leitura técnica, precisa e sem "oba-oba" sobre o potencial de gols de uma partida.

Regras de Ouro:
1. SOBRIEDADE: Não seja "emocionado". Se os dados indicam um jogo aberto, explique o PORQUÊ (ex: defesas vazadas, ataques eficientes). Se houver riscos, aponte-os.
2. FOCO NO DIA: Considere as informações de bastidores (lesões, clima, motivação) e as condições de jogo (Árbitro, Clima, Gramado) como fatores determinantes para validar ou questionar as estatísticas.
3. HISTÓRICO WEB + API: O perfil híbrido (amistosos, eliminatórias, Nations League + jogos de Copa na API) pesa tanto quanto os números do dia — cite quando a web reforça ou contradiz a estatística.
4. RECOMENDAÇÃO ÚNICA: Fundamente a entrada principal (best_pick) com estatística + bastidores + contexto + histórico web.
5. ARBITRAGEM E CLIMA: Analise se o árbitro é do tipo que "deixa o jogo rolar" ou se é rigoroso (o que pode travar o jogo ou gerar expulsões). Veja se o clima (chuva, calor extremo) favorece ou atrapalha o fluxo de gols.
6. ESTRUTURA:
   - Parágrafo 1: O cenário técnico e o momento das equipes (bastidores + histórico recente).
   - Parágrafo 2: A recomendação principal fundamentada (estatística híbrida + condições de jogo).
   - Parágrafo 3: Alertas de risco específicos (ex: "O árbitro rigoroso pode gerar muitas interrupções, dificultando o Over 1.5").

Tom de voz: Profissional, analítico, direto ao ponto. Use termos como 'valor', 'exposição', 'leitura de fluxo', 'equilíbrio'.
Máximo 3 parágrafos, até 1500 caracteres no total. Sempre conclua a última frase — nunca pare no meio.
Não invente dados.
7. ELENCO: Só cite jogadores presentes em home_insights/away_insights. "Não convocado" ≠ "lesionado/fora". Se não estiver nos dados, não mencione.
"""

EXPLANATION_MAX_CHARS = 1500


def _finalize_explanation(text: str, max_chars: int | None = None) -> str:
    """Garante texto completo até o limite de caracteres (corta só em fim de frase)."""
    limit = max_chars or settings.llm_explanation_max_chars
    cleaned = text.strip()
    if len(cleaned) <= limit:
        return cleaned
    chunk = cleaned[:limit]
    for sep in (". ", ".\n", "! ", "? ", ".\""):
        idx = chunk.rfind(sep)
        if idx > limit // 2:
            return chunk[: idx + len(sep)].strip()
    return chunk.rstrip() + "…"

PICK_REFINE_SYSTEM = """Você decide a recomendação de mercado da Palpitaria FC para UM jogo.

Entradas disponíveis:
- numeric_suggestion: leitura inicial baseada só em thresholds (pode ser ajustada)
- home_stats / away_stats: perfil híbrido (API Copa + histórico web — amistosos, eliminatórias)
- home_insights / away_insights: bastidores do dia (lesões, clima interno, motivação)
- match_context: clima, árbitro, gramado
- criteria: filtros anti-zero-gols

Regras:
- A decisão DEVE integrar bastidores e histórico web — não ignore web_factor.
- Mercados: OVER 0.5 GOALS, OVER 1.5 GOALS, OVER 2.5 GOALS, VITÓRIA: [nome do time]
- Se dados web contradizem stats ou há risco alto de 0-0, pode manter mercado conservador ou questionar Over 1.5.
- Não invente jogadores, placares ou clima.
- Só cite jogadores em home_insights/away_insights.

Retorne SOMENTE JSON válido:
{
  "market": "OVER 1.5 GOALS",
  "verdict": "STRONG|CANDIDATE",
  "reason": "2-3 frases objetivas",
  "web_factor": "1 frase: como histórico web + bastidores pesaram"
}
"""

EXCLUDED_PICK_REFINE_SYSTEM = """Você decide o PALPITE ALTERNATIVO da Palpitaria FC para UM jogo FORA do filtro anti-zero-gols.

O jogo foi DESCARTADO para mercados Over (0.5/1.5/2.5). Isso NÃO impede palpite em outros mercados.

Entradas: numeric_suggestion, home_stats, away_stats, home_insights, away_insights, match_context, criteria, exclusion_reasons.

Mercados permitidos (escolha UM):
- VITÓRIA: [nome do time] — favoritismo claro, jogo tende a ser unilateral
- LAY CORRECT SCORE: 0-0 — jogo fechado mas leitura de que haverá pelo menos 1 gol
- LAY CORRECT SCORE: 1-0 — favorito vence magro, padrão de vitória mínima
- LAY CORRECT SCORE: 2-0 — favorito domina sem trocação (BTTS baixo)

Regras:
- NUNCA recomende OVER 0.5/1.5/2.5 — o filtro de gols já vetou.
- Integre bastidores + histórico web.
- Explique por que este mercado faz sentido APESAR do descarte no filtro de gols.
- Não invente dados.

Retorne SOMENTE JSON válido:
{
  "market": "VITÓRIA: Alemanha",
  "verdict": "STRONG|CANDIDATE",
  "reason": "2-3 frases objetivas",
  "web_factor": "1 frase: como histórico web + bastidores pesaram"
}
"""

EXCLUDED_EXPLAIN_SUFFIX = """
NOTA: Este jogo está FORA do filtro anti-zero-gols (Over descartado).
- Parágrafo 1: por que o filtro de gols descartou (critérios que falharam).
- Parágrafo 2: fundamentar o palpite ALTERNATIVO (vitória ou lay correct score) — não mercados Over.
- Parágrafo 3: riscos específicos deste palpite alternativo.
"""


def refine_best_pick(analysis: FixtureAnalysis) -> dict | None:
    """LLM final pick — merges numeric baseline with web stats and backstage."""
    if not analysis.best_pick:
        return None

    if not settings.has_llm:
        return analysis.best_pick

    system = EXCLUDED_PICK_REFINE_SYSTEM if analysis.excluded else PICK_REFINE_SYSTEM
    payload = {
        "home": analysis.home_name,
        "away": analysis.away_name,
        "excluded": analysis.excluded,
        "exclusion_reasons": analysis.exclusion_reasons,
        "goal_potential_score": analysis.goal_potential_score,
        "criteria": [
            {"name": c.name, "value": c.value, "passed": c.passed, "level": c.level, "detail": c.detail}
            for c in analysis.criteria
        ],
        "home_stats": analysis.home_stats_meta,
        "away_stats": analysis.away_stats_meta,
        "home_insights": analysis.home_insights,
        "away_insights": analysis.away_insights,
        "match_context": analysis.match_context,
        "numeric_suggestion": analysis.best_pick,
    }

    try:
        user_content = f"Dados para decisão de mercado:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
        response = chat_completion(system, user_content, temperature=0.25, max_tokens=800)
        parsed = _parse_json_from_llm(response)
        if not parsed or not parsed.get("market"):
            return analysis.best_pick
        refined = {
            "market": parsed["market"],
            "verdict": parsed.get("verdict") or analysis.best_pick.get("verdict", "CANDIDATE"),
            "reason": parsed.get("reason") or analysis.best_pick.get("reason", ""),
            "scope": analysis.best_pick.get("scope", "alternate" if analysis.excluded else "goals"),
        }
        if parsed.get("web_factor"):
            refined["web_factor"] = parsed["web_factor"]
        return refined
    except Exception:
        return analysis.best_pick


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
                "level": c.level,
                "detail": c.detail,
            }
            for c in analysis.criteria
        ],
        "home_insights": analysis.home_insights,
        "away_insights": analysis.away_insights,
        "home_stats": analysis.home_stats_meta,
        "away_stats": analysis.away_stats_meta,
        "best_pick": analysis.best_pick,
        "match_context": analysis.match_context,
    }

    if not settings.has_llm:
        return _fallback_explanation(analysis)

    system = SYSTEM_PROMPT
    if analysis.excluded and analysis.best_pick:
        system += EXCLUDED_EXPLAIN_SUFFIX

    try:
        user_content = f"Dados do jogo:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
        text = chat_completion(
            system,
            user_content,
            max_tokens=settings.llm_explanation_max_tokens,
        )
        if text:
            return _finalize_explanation(text)
        return _fallback_explanation(analysis)
    except Exception as exc:
        hint = llm_config_hint(exc)
        return f"{_fallback_explanation(analysis)}\n\n(LLM indisponível: {hint})"


def _fallback_explanation(analysis: FixtureAnalysis) -> str:
    if analysis.excluded:
        reasons = "; ".join(analysis.exclusion_reasons) or "critérios não atendidos"
        base = (
            f"Fora do filtro de gols: {analysis.home_name} x {analysis.away_name}. "
            f"Motivos: {reasons}. "
            f"Score de potencial: {analysis.goal_potential_score}/100."
        )
        pick = analysis.best_pick
        if pick:
            return (
                f"{base} "
                f"Palpite alternativo: {pick.get('market', '—')} ({pick.get('verdict', '—')}). "
                f"{pick.get('reason', '')}"
            )
        return base

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
