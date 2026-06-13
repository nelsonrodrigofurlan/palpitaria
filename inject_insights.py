import json
from datetime import datetime
from palpitaria.database import SessionLocal
from palpitaria.models import Team, TeamProfile

INSIGHTS = {
    7: { # Brazil
        "sentiment": "positivo",
        "key_insights": ["Neymar fora (lesão grau 2)", "Ancelotti nunca repetiu escalação", "Danilo titular inédito"],
        "backstage_info": "Ancelotti mantendo mistério, clima de 'metamorfose' na busca pelo hexa.",
        "confidence_score": 90
    },
    29: { # Morocco
        "sentiment": "negativo",
        "key_insights": ["Troca de técnico recente (Ouahbi)", "Nayef Aguerd lesionado", "Abde Ezzalzouli lesionado"],
        "backstage_info": "Legado de 2022 pesa, mas as mudanças de última hora geram incerteza.",
        "confidence_score": 85
    },
    19: { # Switzerland
        "sentiment": "positivo",
        "key_insights": ["Ruben Vargas recuperado", "Elenco experiente (Xhaka, Akanji)", "Formação 4-2-3-1"],
        "backstage_info": "Estabilidade e confiança para a estreia contra o Qatar.",
        "confidence_score": 95
    },
    41: { # Qatar
        "sentiment": "positivo",
        "key_insights": ["Julen Lopetegui no comando", "Akram Afif é o craque", "Elenco base da Aspire Academy"],
        "backstage_info": "Pressão para provar que o título asiático não foi sorte.",
        "confidence_score": 80
    }
}

def inject():
    db = SessionLocal()
    for team_id, insights in INSIGHTS.items():
        profile = TeamProfile(
            team_id=team_id,
            computed_at=datetime.utcnow(),
            matches_sampled=1,
            avg_goals_scored=2.0,
            avg_goals_conceded=1.0,
            zero_zero_rate=0.0,
            over_05_rate=1.0,
            over_15_rate=0.8,
            both_teams_score_rate=0.7,
            insights_json=json.dumps(insights),
            raw_json="{}"
        )
        db.add(profile)
    db.commit()
    db.close()
    print("Injection complete!")

if __name__ == "__main__":
    inject()
