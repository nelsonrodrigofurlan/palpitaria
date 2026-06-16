from __future__ import annotations
from sqlalchemy.orm import Session
from palpitaria.models import ApiConfig
from palpitaria.config import settings

def get_api_config(db: Session, key: str, default: str = "") -> str:
    """Busca configuração no banco, fallback para o settings (env)."""
    # Prioridade para Variáveis de Ambiente (Segurança Máxima no Cloud Run)
    if key == "FOOTBALL_DATA_TOKEN":
        return settings.football_data_token or default
    if key == "OPENAI_API_KEY":
        return settings.openai_api_key or default
    
    # Outras configs podem vir do banco
    cfg = db.query(ApiConfig).filter_by(key=key).first()
    if cfg and cfg.value:
        return cfg.value
    
    # Fallback final para settings
    if key == "OPENAI_BASE_URL":
        return settings.openai_base_url or default
    
    return default
