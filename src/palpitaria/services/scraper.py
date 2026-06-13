from __future__ import annotations
import json
from palpitaria.services.llm_client import chat_completion

SYSTEM_PROMPT = """Você é um especialista em inteligência de bastidores de futebol.
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

def analyze_team_moment(team_name: str, raw_content: str) -> dict:
    user_content = f"Notícias e informações brutas sobre a seleção do {team_name}:\n\n{raw_content}"
    try:
        response = chat_completion(SYSTEM_PROMPT, user_content)
        # Attempt to parse JSON from LLM response
        # In a real scenario, we'd use a more robust parser or structured output
        start = response.find("{")
        end = response.rfind("}") + 1
        if start != -1 and end > 0:
            return json.loads(response[start:end])
        return {"error": "Failed to parse LLM response"}
    except Exception as e:
        return {"error": str(e)}

def get_search_query(team_name: str) -> str:
    return f"últimas notícias seleção {team_name} bastidores lesões escalação"
