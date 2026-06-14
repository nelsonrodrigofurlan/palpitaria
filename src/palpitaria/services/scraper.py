from __future__ import annotations

import json

from sqlalchemy.orm import Session

from palpitaria.services.analyzer import default_match_context
from palpitaria.services.team_names import english_team_name

TEAM_SYSTEM_PROMPT = """Você é um especialista em inteligência de bastidores de futebol.
Analise notícias e extraia o "momento" de uma seleção.

Foque em:
1. Lesões e suspensões de última hora (apenas de jogadores CONVOCADOS).
2. Clima no vestiário (motivação, crises, união).
3. Prováveis mudanças táticas.
4. Fatores externos (clima, torcida, pressão da imprensa).

Regras anti-alucinação (OBRIGATÓRIO):
- Cite APENAS jogadores mencionados explicitamente nas fontes fornecidas.
- NUNCA invente desfalques, lesões ou convocações.
- "Não convocado" é diferente de "lesionado/fora da Copa" — use o termo correto.
- Ao mencionar o adversário, só use fatos confirmados nas fontes; não especule sobre elenco rival.
- Se um dado não estiver nas fontes, omita — não preencha com memória ou suposição.
- Prefira menos bullets corretos a muitos bullets inventados.

Retorne SOMENTE JSON válido:
{
  "sentiment": "positivo/neutro/negativo",
  "key_insights": ["lista de pontos principais, cada um factual"],
  "backstage_info": "resumo factual do que está acontecendo por trás das câmeras",
  "confidence_score": 0-100
}
"""


def search_web(query: str, max_results: int = 6) -> str:
    """Busca snippets na web (DuckDuckGo) para alimentar o LLM."""
    try:
        try:
            from ddgs import DDGS
        except ImportError:
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


def search_web_fallbacks(queries: list[str], max_results: int = 6) -> str:
    for query in queries:
        snippets = search_web(query, max_results=max_results)
        if snippets and not snippets.startswith("(Busca indisponível") and len(snippets) > 80:
            return snippets
    return ""


def get_search_queries(team_name: str, *, external_id: int | None = None) -> list[str]:
    en = english_team_name(team_name, external_id)
    return [
        f"{en} national team World Cup 2026 injuries lineup squad news",
        f"{team_name} seleção Copa do Mundo 2026 lesões escalação bastidores",
    ]


def get_match_context_queries(home_name: str, away_name: str) -> list[str]:
    return [
        f"{home_name} vs {away_name} World Cup 2026 referee weather stadium pitch forecast",
        f"{home_name} {away_name} FIFA World Cup 2026 match officials MetLife",
        f"{home_name} x {away_name} Copa 2026 árbitro clima gramado estádio",
    ]


def analyze_team_moment(team_name: str, raw_content: str, *, squad: list[str] | None = None) -> dict:
    squad_block = ""
    if squad:
        squad_block = (
            f"\n\n--- LISTA OFICIAL DE CONVOCADOS ({team_name}) — fonte API ---\n"
            f"{', '.join(squad)}\n\n"
            "REGRA: jogador FORA desta lista NÃO foi convocado — NÃO cite como desfalque ou lesionado. "
            "Desfalque = convocado indisponível (lesão/suspensão confirmada nas fontes)."
        )
    user_content = f"Notícias e informações brutas sobre a seleção do {team_name}:\n\n{raw_content}{squad_block}"
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


def _parse_json_from_llm(response: str) -> dict | None:
    cleaned = response.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    start = cleaned.find("{")
    end = cleaned.rfind("}") + 1
    if start != -1 and end > start:
        try:
            return json.loads(cleaned[start:end])
        except json.JSONDecodeError:
            return None
    return None


def analyze_match_context(home_name: str, away_name: str, raw_content: str) -> dict:
    system_prompt = """Você é um analista de condições de jogo.
Extraia clima, árbitro e gramado para a partida a partir das fontes.
Se houver previsão do tempo, cite temperatura/condição. Se houver estádio, infira gramado padrão FIFA.
Use apenas o que aparecer nas fontes. Se não houver dado, diga "Não encontrado nas fontes".

Retorne SOMENTE JSON válido (sem markdown):
{
  "weather": "descrição curta",
  "referee": "nome e estilo ou Não encontrado nas fontes",
  "pitch": "estado do gramado ou Normal (estádio FIFA)",
  "impact": "impacto no fluxo de gols"
}
"""
    user_content = f"Informações brutas sobre o jogo {home_name} x {away_name}:\n\n{raw_content}"
    try:
        response = chat_completion(system_prompt, user_content, max_tokens=1500, temperature=0.2)
        parsed = _parse_json_from_llm(response)
        if parsed:
            return parsed
        return {"weather": "Desconhecido", "referee": "Não informado", "pitch": "Normal"}
    except Exception:
        return {"weather": "Desconhecido", "referee": "Não informado", "pitch": "Normal"}


def fetch_api_match_context(external_id: int) -> dict:
    """Árbitro e estádio via football-data.org (quando disponível)."""
    from palpitaria.config import settings
    from palpitaria.services.football_data_client import FootballDataClient

    if not settings.has_football_token:
        return {}

    try:
        client = FootballDataClient()
        match = client._get(f"/matches/{external_id}")
    except Exception:
        return {}

    ctx: dict = {}
    refs = match.get("referees") or []
    main_ref = next((r for r in refs if r.get("type") == "REFEREE"), None)
    if main_ref:
        name = main_ref.get("name", "")
        nat = main_ref.get("nationality", "")
        ctx["referee"] = f"{name} ({nat}) — designado pela FIFA" if nat else name

    venue = match.get("venue") or ""
    if venue:
        ctx["pitch"] = f"{venue} — gramado oficial FIFA"
    return ctx


def fetch_team_squad(external_id: int) -> list[str]:
    from palpitaria.config import settings
    from palpitaria.services.football_data_client import FootballDataClient

    if not settings.has_football_token:
        return []
    try:
        client = FootballDataClient()
        payload = client._get(f"/teams/{external_id}")
        return [p.get("name", "") for p in (payload.get("squad") or []) if p.get("name")]
    except Exception:
        return []


def _normalize_match_context(raw: dict) -> dict:
    return {
        "weather": raw.get("weather") or "Não encontrado nas fontes",
        "referee": raw.get("referee") or "Não informado",
        "pitch": raw.get("pitch") or "Normal",
        **({"impact": raw["impact"]} if raw.get("impact") else {}),
    }


def collect_match_context(home_name: str, away_name: str, *, external_id: int | None = None) -> dict:
    ctx = fetch_api_match_context(external_id) if external_id else {}

    snippets = search_web_fallbacks(get_match_context_queries(home_name, away_name), max_results=8)
    if snippets:
        web_ctx = _normalize_match_context(analyze_match_context(home_name, away_name, snippets))
        for key in ("weather", "referee", "pitch", "impact"):
            if not ctx.get(key) or "Aguardando" in str(ctx.get(key, "")):
                if web_ctx.get(key) and "Não encontrado" not in str(web_ctx.get(key)):
                    ctx[key] = web_ctx[key]
                elif key not in ctx and web_ctx.get(key):
                    ctx[key] = web_ctx[key]

    if not ctx:
        return default_match_context()
    return _normalize_match_context(ctx)


def collect_team_insights(team_name: str, *, team_external_id: int | None = None) -> dict | None:
    snippets = search_web_fallbacks(
        get_search_queries(team_name, external_id=team_external_id), max_results=6
    )
    if not snippets:
        return None
    squad = fetch_team_squad(team_external_id) if team_external_id else []
    insights = analyze_team_moment(team_name, snippets, squad=squad)
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
    from palpitaria.models import Team

    team = db.query(Team).filter_by(id=team_id).one_or_none()
    external_id = team.external_id if team else None
    insights = collect_team_insights(team_name, team_external_id=external_id)
    if not insights:
        return None
    update_team_insights(db, team_id, insights)
    return insights


def enrich_fixture_analysis(
    db: Session,
    *,
    fixture_id: int,
    external_id: int | None,
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
        log("  [2/3] Coletando bastidores + contexto (mesmo descartado — informa a decisão)...")
    else:
        log("  [2/3] Scraping bastidores + contexto de jogo...")

    log(f"  [2a] Bastidores — {home_name}...")
    refreshed_home = refresh_team_insights(db, home_team_id, home_name)
    home = refreshed_home or home_insights

    log(f"  [2b] Bastidores — {away_name}...")
    refreshed_away = refresh_team_insights(db, away_team_id, away_name)
    away = refreshed_away or away_insights

    match_context = None
    log(f"  [2c] Contexto de jogo — clima, árbitro, gramado...")
    match_context = collect_match_context(home_name, away_name, external_id=external_id)

    return home, away, match_context
