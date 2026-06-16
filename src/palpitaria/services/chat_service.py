from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

from palpitaria.models import UserInsight, Team
from palpitaria.services.llm_client import chat_completion
from palpitaria.services.llm_utils import _parse_json_from_llm

AUDITOR_SYSTEM_PROMPT = """Você é o Auditor Sênior da Palpitaria FC. Sua função é ouvir as percepções de todos os usuários e filtrá-las com RIGOR EXTREMO antes de integrá-las à base de conhecimento global da IA.

Pilares Inegociáveis do Produto:
1. CAUTELA: Evitamos riscos desnecessários. Se a informação for vaga, descarte.
2. SOBRIEDADE: Ignoramos 100% de emoção, clubismo ou "pressentimentos".
3. FUNDAMENTAÇÃO TÉCNICA: Só aceitamos fatos (desfalques, mudanças táticas confirmadas, condições de campo, clima local, bastidores reportados).
4. SEGURANÇA: A base de conhecimento deve ser puríssima. É melhor descartar uma informação boa do que aceitar uma duvidosa.

Sua Missão:
- Analise a mensagem de qualquer usuário.
- Seja um "filtro impiedoso": marque como `is_valid: true` APENAS se a informação for um fato técnico ou bastidor concreto que impacte o potencial de gols ou resultado.
- Se houver QUALQUER tom de torcida ("vamos ganhar", "vai ser massacre", "o juiz sempre rouba"), marque como `is_valid: false`.
- Informações genéricas ("o time é bom") também são descartadas (`is_valid: false`).
- A IA usará essas percepções validadas para "aprender" e ajustar as análises futuras de todos os usuários.

Retorne SOMENTE JSON:
{
  "is_valid": true|false,
  "evaluation": "Sua análise técnica, curta e extremamente rigorosa",
  "identified_team_id": null|int,
  "response": "Sua resposta ao usuário (educada, mas firme e analítica)"
}
"""

def process_user_message(db: Session, message: str, user_id: int | None = None) -> dict:
    # Buscar times para ajudar o LLM a identificar
    teams = db.query(Team).all()
    team_list = [{"id": t.id, "name": t.name} for t in teams]
    
    user_content = f"Mensagem do Usuário: {message}\n\nLista de Times:\n{json.dumps(team_list, ensure_ascii=False)}"
    
    try:
        response = chat_completion(AUDITOR_SYSTEM_PROMPT, user_content, temperature=0.2)
        parsed = _parse_json_from_llm(response)
        
        if not parsed:
            return {
                "response": "Não consegui processar sua mensagem agora. Tente ser mais específico sobre um time ou jogo.",
                "is_valid": False
            }
            
        # Salvar na base de conhecimento se for algo relevante
        insight = UserInsight(
            user_id=user_id,
            content=message,
            evaluation=parsed.get("evaluation"),
            is_valid=parsed.get("is_valid", False),
            team_id=parsed.get("identified_team_id")
        )
        db.add(insight)
        db.commit()
        
        return parsed
        
    except Exception as e:
        return {
            "response": f"Erro ao processar: {str(e)}",
            "is_valid": False
        }

def get_valid_insights_for_team(db: Session, team_id: int) -> list[str]:
    """Recupera percepções validadas para injetar na análise."""
    insights = db.query(UserInsight).filter(
        UserInsight.team_id == team_id,
        UserInsight.is_valid == True
    ).order_by(UserInsight.created_at.desc()).limit(5).all()
    
    return [i.content for i in insights]
