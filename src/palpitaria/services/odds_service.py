import json

import httpx
from sqlalchemy.orm import Session

from palpitaria.config import settings
from palpitaria.services.team_names import localize_team_name

SPORT_KEY_BY_COMPETITION = {
    "BSA": "soccer_brazil_campeonato",
    "BSB": "soccer_brazil_serie_b",
    "WC": "soccer_fifa_world_cup",
    "PL": "soccer_epl",
    "PD": "soccer_spain_la_liga",
    "BL1": "soccer_germany_bundesliga",
    "SA": "soccer_italy_serie_a",
    "FL1": "soccer_france_ligue_one",
    "CL": "soccer_uefa_champions_league",
    "EL": "soccer_uefa_europa_league",
}

def fetch_odds_api_data(sport="soccer_brazil_campeonato_serie_a", regions="eu", markets="h2h,totals"):
    """
    Busca odds via The Odds API.
    Ligas comuns: 
    - soccer_brazil_campeonato_serie_a
    - soccer_fifa_world_cup (quando disponível)
    """
    if not settings.odds_api_key:
        return {"error": "ODDS_API_KEY não configurada"}

    url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds"
    params = {
        "apiKey": settings.odds_api_key,
        "regions": regions,
        "markets": markets,
        "oddsFormat": "decimal",
    }

    try:
        with httpx.Client() as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        return {"error": str(e)}

def extract_betfair_odds(odds_data):
    """Filtra as odds especificamente da Betfair Exchange."""
    results = []
    for game in odds_data:
        home_pt = localize_team_name(game.get("home_team"))
        away_pt = localize_team_name(game.get("away_team"))
        game_info = {
            "id": game.get("id"),
            "home_team": home_pt,
            "away_team": away_pt,
            "commence_time": game.get("commence_time"),
            "betfair_ex": None
        }
        
        for bookmaker in game.get("bookmakers", []):
            if bookmaker.get("key").startswith("betfair_ex"):
                # Traduzir nomes nos outcomes para bater com o app
                markets = bookmaker.get("markets", [])
                for mkt in markets:
                    for outcome in mkt.get("outcomes", []):
                        name = outcome.get("name")
                        if name == game.get("home_team"):
                            outcome["name"] = home_pt
                        elif name == game.get("away_team"):
                            outcome["name"] = away_pt
                        elif name.lower() == "draw":
                            outcome["name"] = "Empate"
                
                game_info["betfair_ex"] = markets
                break
        
        if game_info["betfair_ex"]:
            results.append(game_info)

    return results


def update_competition_odds(db: Session, comp_code: str) -> None:
    """Busca odds da Betfair e salva no cache da competição."""
    from palpitaria.models import Competition

    sport_key = SPORT_KEY_BY_COMPETITION.get(comp_code)
    if not sport_key:
        return

    raw_odds = fetch_odds_api_data(sport=sport_key)
    if isinstance(raw_odds, list):
        odds_list = extract_betfair_odds(raw_odds)
        comp = db.query(Competition).filter_by(code=comp_code).first()
        if comp:
            comp.odds_json = json.dumps(odds_list)
            db.commit()
