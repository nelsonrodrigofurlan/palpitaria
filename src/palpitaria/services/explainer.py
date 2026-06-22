from __future__ import annotations

import json
import re

from palpitaria.config import settings
from types import SimpleNamespace

from palpitaria.services.analyzer import FixtureAnalysis, infer_favorite, profile_from_meta
from palpitaria.services.llm_client import chat_completion, llm_config_hint
from palpitaria.services.llm_utils import _parse_json_from_llm

SYSTEM_PROMPT = """Você é o analista sênior da Palpitaria FC. Escreva a leitura pré-jogo em português do Brasil.

FORMATO OBRIGATÓRIO (não negocie):
- Exatamente 2 ou 3 parágrafos em prosa corrida.
- Sem títulos, sem bullets, sem asteriscos, sem markdown, sem listas numeradas.
- Sem inglês. Não use termos como "best pick", "grounded", "Historical Web", "mentioned".
- Cada parágrafo com 2–4 frases completas, terminando em ponto final.
- Máximo 1500 caracteres no total. Sempre conclua a última frase.

CONTEÚDO:
- Parágrafo 1: cenário técnico — momento das equipes, bastidores e histórico híbrido (web + API).
- Parágrafo 2: recomendação principal (best_pick) fundamentada em estatística + contexto (árbitro, clima, gramado).
- Parágrafo 3 (opcional): riscos e alertas específicos.

Tom: profissional, analítico, direto — sem "oba-oba". Não invente dados.
Só cite jogadores presentes em home_insights/away_insights.
Se houver user_insights validados, use como reforço — não como fonte única.

EXEMPLO DE FORMATO (não copie o conteúdo):
Portugal chega com média elevada de gols nos últimos jogos e defesa que concede espaço. O histórico híbrido confirma tendência de jogos abertos, reforçado pelos bastidores do dia.

A leitura aponta vitória portuguesa com jogo movimentado: o árbitro costuma deixar fluir e o clima não deve travar o ritmo. A exposição em Over 1.5 ganha suporte nos números combinados.

Risco: se o Congo fechar demais no primeiro tempo, o fluxo de gols pode atrasar — mas o favorito tem volume ofensivo para destravar após o intervalo.
"""

EXPLANATION_RETRY_SUFFIX = """

CORREÇÃO OBRIGATÓRIA: sua resposta anterior foi rejeitada (formato inválido, inglês ou texto incompleto).
Reescreva AGORA em português do Brasil, somente prosa em parágrafos, sem markdown e sem inglês.
"""

# Vazamento típico quando o modelo ignora o prompt e espelha instruções em inglês.
_BAD_EXPLANATION_MARKERS = (
    "historical web",
    "best pick",
    "grounded",
    "referee, climate",
    "climate not found",
    "mentioned.",
    "web_factor",
    "no invente",
)

_MARKDOWN_LIST_RE = re.compile(r"(?m)^\s*[\*\-•]\s+")
_INCOMPLETE_END_RE = re.compile(r"[\*\-:,]$")
_VALID_END_RE = re.compile(r'[.!?…]["\']?\s*$')

EXPLANATION_MAX_CHARS = 1500


def _strip_markdown_noise(text: str) -> str:
    """Remove bullets/asteriscos comuns em respostas fora do formato."""
    lines = []
    for line in text.splitlines():
        cleaned = _MARKDOWN_LIST_RE.sub("", line.strip())
        cleaned = re.sub(r"\*+", "", cleaned).strip()
        if cleaned:
            lines.append(cleaned)
    merged = " ".join(lines)
    merged = re.sub(r"\s{2,}", " ", merged).strip()
    return merged


def _explanation_quality_issues(text: str) -> list[str]:
    """Retorna motivos de rejeição; lista vazia = aceitável."""
    issues: list[str] = []
    cleaned = text.strip()
    if len(cleaned) < 120:
        issues.append("muito_curto")
    if _MARKDOWN_LIST_RE.search(cleaned):
        issues.append("markdown_lista")
    if cleaned.count("*") >= 2:
        issues.append("asteriscos")
    lower = cleaned.lower()
    if any(marker in lower for marker in _BAD_EXPLANATION_MARKERS):
        issues.append("ingles_ou_vazamento_prompt")
    if _INCOMPLETE_END_RE.search(cleaned):
        issues.append("termino_incompleto")
    if not _VALID_END_RE.search(cleaned):
        issues.append("sem_frase_final")
    pt_hints = sum(1 for w in (" de ", " da ", " do ", " que ", " com ", " jogo", " gol") if w in lower)
    if len(cleaned) > 150 and pt_hints < 2:
        issues.append("pouco_portugues")
    return issues


def _is_acceptable_explanation(text: str) -> bool:
    return not _explanation_quality_issues(text)


def _finalize_explanation(text: str, max_chars: int | None = None) -> str:
    """Garante texto completo até o limite de caracteres (corta só em fim de frase)."""
    limit = max_chars or settings.llm_explanation_max_chars
    cleaned = _strip_markdown_noise(text.strip())
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
Mesmo formato: só prosa em português, 2–3 parágrafos, sem markdown.
- Parágrafo 1: por que o filtro de gols descartou.
- Parágrafo 2: fundamentar o palpite alternativo (vitória ou lay correct score).
- Parágrafo 3: riscos do palpite alternativo.
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
        response = chat_completion(system, user_content, temperature=0.25, max_tokens=800, feature="explainer_pick")
        parsed = _parse_json_from_llm(response)
        if not parsed or not parsed.get("market"):
            return analysis.best_pick
        market = parsed["market"]
        home_p = profile_from_meta(analysis.home_stats_meta)
        away_p = profile_from_meta(analysis.away_stats_meta)
        if market.startswith("VITÓRIA:") and home_p and away_p:
            picked = market.replace("VITÓRIA:", "", 1).strip()
            fav = infer_favorite(analysis, home_p, away_p)
            if fav and picked != fav.name:
                return analysis.best_pick
        refined = {
            "market": market,
            "verdict": parsed.get("verdict") or analysis.best_pick.get("verdict", "CANDIDATE"),
            "reason": parsed.get("reason") or analysis.best_pick.get("reason", ""),
            "scope": analysis.best_pick.get("scope", "alternate" if analysis.excluded else "goals"),
        }
        if parsed.get("web_factor"):
            refined["web_factor"] = parsed["web_factor"]
        return refined
    except Exception:
        return analysis.best_pick


def _compose_explanation_from_analysis(analysis: FixtureAnalysis) -> str:
    """Fallback em português quando o LLM falha no formato."""
    pick = analysis.best_pick or {}
    ctx = analysis.match_context or {}
    market = pick.get("market", "—")
    reason = pick.get("reason", "").strip()
    web = pick.get("web_factor", "").strip()

    referee = (ctx.get("referee") or "").strip()
    weather = (ctx.get("weather") or "").strip()
    pitch = (ctx.get("pitch") or "").strip()
    context_bits = [b for b in (
        f"Árbitro: {referee}" if referee and referee != "Aguardando coleta" else "",
        f"Clima: {weather}" if weather and "aguardando" not in weather.lower() else "",
        f"Gramado: {pitch}" if pitch and "aguardando" not in pitch.lower() else "",
    ) if b]

    p1 = (
        f"{analysis.home_name} x {analysis.away_name} — score de potencial {analysis.goal_potential_score}/100. "
        f"O histórico híbrido (API + web) e os bastidores do dia sustentam a leitura abaixo."
    )
    p2 = f"Recomendação: {market}."
    if reason:
        p2 += f" {reason}"
    if web:
        p2 += f" {web}"
    p3 = ""
    if context_bits:
        p3 = "Contexto de jogo: " + "; ".join(context_bits) + "."
    if analysis.excluded and analysis.exclusion_reasons:
        p3 = (
            (p3 + " " if p3 else "")
            + "Alerta: jogo fora do filtro de gols — "
            + "; ".join(analysis.exclusion_reasons[:2])
            + "."
        )
    parts = [p for p in (p1, p2, p3) if p]
    return _finalize_explanation("\n\n".join(parts))


def _request_explanation(system: str, user_content: str, *, retry: bool = False) -> str:
    suffix = EXPLANATION_RETRY_SUFFIX if retry else ""
    return chat_completion(
        system,
        user_content + suffix,
        temperature=0.2 if retry else 0.35,
        max_tokens=settings.llm_explanation_max_tokens,
        feature="explainer",
    )


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

    user_content = (
        "Dados do jogo (gere a leitura em português, só prosa em parágrafos):\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )

    try:
        for attempt, is_retry in enumerate((False, True)):
            raw = _request_explanation(system, user_content, retry=is_retry)
            if not raw:
                continue
            candidate = _finalize_explanation(raw)
            if _is_acceptable_explanation(candidate):
                return candidate
        return _compose_explanation_from_analysis(analysis)
    except Exception as exc:
        hint = llm_config_hint(exc)
        return f"{_compose_explanation_from_analysis(analysis)}\n\n(LLM indisponível: {hint})"


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
