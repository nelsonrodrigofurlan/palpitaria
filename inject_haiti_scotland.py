import json
from datetime import datetime
from palpitaria.database import SessionLocal
from palpitaria.models import Team, TeamProfile

INSIGHTS = {
    34: { # Haiti
        "sentiment": "neutro",
        "key_insights": ["Retorno após 52 anos", "Leverton Pierre cortado (adutor)", "Derrick Etienne pode ser titular"],
        "backstage_info": "Clima de festa pelo retorno, mas o corte de Pierre no meio-campo é um golpe duro na organização.",
        "confidence_score": 75
    },
    47: { # Scotland
        "sentiment": "positivo",
        "key_insights": ["McTominay confirmado", "Billy Gilmour fora da Copa", "Scott McKenna fora da estreia"],
        "backstage_info": "Favoritismo claro; time focado em quebrar o tabu de nunca ter passado da fase de grupos.",
        "confidence_score": 88
    }
}

def inject():
    db = SessionLocal()
    for team_id, insights in INSIGHTS.items():
        profile = TeamProfile(
            team_id=team_id,
            computed_at=datetime.utcnow(),
            matches_sampled=1,
            avg_goals_scored=1.5,
            avg_goals_conceded=1.5,
            zero_zero_rate=0.0,
            over_05_rate=1.0,
            over_15_rate=0.6,
            both_teams_score_rate=0.5,
            insights_json=json.dumps(insights),
            raw_json="{}"
        )
        db.add(profile)
    db.commit()
    db.close()
    print("Injection complete for Haiti and Scotland!")

if __name__ == "__main__":
    inject()
