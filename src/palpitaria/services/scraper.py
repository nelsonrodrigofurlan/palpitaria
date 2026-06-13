from __future__ import annotations

import json

from sqlalchemy.orm import Session

from palpitaria.services.analyzer import default_match_context
from palpitaria.services.llm_client import chat_completion

TEAM_SYSTEM_PROMPT = """Você é um especialista em inteligência de bastidores de futebol.
Sua tarefa é analisar notícias, redes sociais e informações de bastidores para extrair o "momento" de uma seleção.
Foque em:
1. Lesões e suspensões de última hora.
2. Clima no vestiário (motivação, crises, união).
3. Prováveis mudanças táticas.
4. Fatores externos (clima, torcida, pressão da imprensa).

Retorne um JSON com:
{
  "sentiment": "positivo/neutro/negativo",
  "key_insights": ["lista de pontos principais"],
  "backstage_info": "resumo do que está acontecendo por trás das câmeras",
  "confidence_score": 0-100
}
"""


def search_web(query: str, max_results: int = 6) -> str:
    """Busca snippets na web (DuckDuckGo) para alimentar o LLM."""
    try:
        from duckduckgo_search import DDGS

        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        if not results:
            return ""
        lines = []
        for item in results:
            title = item.get("title", "")
            body = item.get("body", "")
            href = item.get("href", "")
            lines.append(f"- {title}\n  {body}\n  {href}")
        return "\n".join(lines)
    except Exception as exc:
        return f"(Busca indisponível: {exc})"


def get_search_query(team_name: str) -> str:
    return f"Copa do Mundo 2026 seleção {team_name} bastidores lesões escalação últimas notícias"


def get_match_context_query(home_name: str, away_name: str) -> str:
    return (
        f"Copa do Mundo 2026 {home_name} vs {away_name} "
        f"árbitro designado clima previsão do tempo gramado estádio local"
    )


def analyze_team_moment(team_name: str, raw_content: str) -> dict:
    user_content = f"Notícias e informações brutas sobre a seleção do {team_name}:\n\n{raw_content}"
    try:
        response = chat_completion(TEAM_SYSTEM_PROMPT, user_content, max_tokens=2000)
        cleaned_response = response.strip()
        start = cleaned_response.find("{")
        end = cleaned_response.rfind("}") + 1

        if start != -1 and end > 0:
            json_str = cleaned_response[start:end]
            try:
                return json.loads(json_str)
            except json.JSONDecodeError as exc:
                return {"error": f"JSON parse error: {exc}"}

        return {"error": "No JSON found in LLM response"}
    except Exception as exc:
        return {"error": str(exc)}


def analyze_match_context(home_name: str, away_name: str, raw_content: str) -> dict:
    system_prompt = """Você é um analista de condições de jogo.
Sua tarefa é extrair informações sobre o clima, o árbitro e o estado do gramado para uma partida específica.
Use apenas o que aparecer nas fontes. Se não houver dado, diga "Não encontrado nas fontes".

Retorne um JSON com:
{
  "weather": "descrição curta do clima (ex: Sol, 25°C, sem vento)",
  "referee": "nome do árbitro e estilo (ex: Wilton Sampaio - rigoroso, média alta de cartões)",
  "pitch": "estado do gramado (ex: Excelente, tapete)",
  "impact": "como essas condições podem afetar o fluxo de gols"
}
"""
    user_content = f"Informações brutas sobre o jogo {home_name} x {away_name}:\n\n{raw_content}"
    try:
        response = chat_completion(system_prompt, user_content, max_tokens=1200)
        start = response.find("{")
        end = response.rfind("}") + 1
        if start != -1 and end > 0:
            return json.loads(response[start:end])
        return {"weather": "Desconhecido", "referee": "Não informado", "pitch": "Normal"}
    except Exception:
        return {"weather": "Desconhecido", "referee": "Não informado", "pitch": "Normal"}


def _normalize_match_context(raw: dict) -> dict:
    return {
        "weather": raw.get("weather") or "Não encontrado nas fontes",
        "referee": raw.get("referee") or "Não informado",
        "pitch": raw.get("pitch") or "Normal",
        **({"impact": raw["impact"]} if raw.get("impact") else {}),
    }


def collect_match_context(home_name: str, away_name: str) -> dict:
    query = get_match_context_query(home_name, away_name)
    snippets = search_web(query, max_results=8)
    if not snippets or snippets.startswith("(Busca indisponível"):
        return default_match_context()
    return _normalize_match_context(analyze_match_context(home_name, away_name, snippets))


def collect_team_insights(team_name: str) -> dict | None:
    query = get_search_query(team_name)
    snippets = search_web(query, max_results=6)
    if not snippets or snippets.startswith("(Busca indisponível"):
        return None
    insights = analyze_team_moment(team_name, snippets)
    if "error" in insights:
        return None
    return insights


def update_team_insights(db: Session, team_id: int, insights: dict) -> bool:
    from sqlalchemy import desc

    from palpitaria.models import TeamProfile

    profile = (
        db.query(TeamProfile)
        .filter_by(team_id=team_id)
        .order_by(desc(TeamProfile.computed_at))
        .first()
    )
    if profile:
        profile.insights_json = json.dumps(insights, ensure_ascii=False)
        db.commit()
        return True

    new_profile = TeamProfile(team_id=team_id, insights_json=json.dumps(insights, ensure_ascii=False))
    db.add(new_profile)
    db.commit()
    return True


def refresh_team_insights(db: Session, team_id: int, team_name: str) -> dict | None:
    insights = collect_team_insights(team_name)
    if not insights:
        return None
    update_team_insights(db, team_id, insights)
    return insights


def enrich_fixture_analysis(
    db: Session,
    *,
    fixture_id: int,
    home_team_id: int,
    away_team_id: int,
    home_name: str,
    away_name: str,
    excluded: bool,
    home_insights: dict | None,
    away_insights: dict | None,
    log_callback=None,
) -> tuple[dict | None, dict | None, dict | None]:
    """Coleta bastidores + contexto de jogo antes da recomendação LLM."""

    def log(msg: str) -> None:
        if log_callback:
            log_callback(msg)

    if excluded:
        log("  [2/3] Jogo descartado — scrap ignorado.")
        return home_insights, away_insights, None

    log(f"  [2a] Bastidores — {home_name}...")
    refreshed_home = refresh_team_insights(db, home_team_id, home_name)
    home = refreshed_home or home_insights

    log(f"  [2b] Bastidores — {away_name}...")
    refreshed_away = refresh_team_insights(db, away_team_id, away_name)
    away = refreshed_away or away_insights

    match_context = None
    log(f"  [2c] Contexto de jogo — clima, árbitro, gramado...")
    match_context = collect_match_context(home_name, away_name)

    return home, away, match_context
