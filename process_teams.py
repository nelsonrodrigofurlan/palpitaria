import sys
import json
import os
from palpitaria.database import SessionLocal
from palpitaria.models import Team, TeamProfile
from palpitaria.services.scraper import analyze_team_moment, update_team_insights

def process_team(team_name, raw_content):
    db = SessionLocal()
    try:
        # Find team_id
        team = db.query(Team).filter(Team.name == team_name).first()
        if not team:
            print(f"Team {team_name} not found in database.")
            return

        print(f"Analyzing moment for {team_name}...")
        insights = analyze_team_moment(team_name, raw_content)
        
        if "error" in insights:
            print(f"Error analyzing {team_name}: {insights['error']}")
            return

        print(f"Updating insights for {team_name} (ID: {team.id})...")
        success = update_team_insights(db, team.id, insights)
        
        if success:
            print(f"Successfully updated {team_name}.")
        else:
            print(f"Failed to update {team_name}. No TeamProfile found?")
            # Create a profile if it doesn't exist? The user said "mais recente", 
            # implying it should exist. But let's check.
            profile = db.query(TeamProfile).filter_by(team_id=team.id).first()
            if not profile:
                print(f"Creating new TeamProfile for {team_name}...")
                new_profile = TeamProfile(team_id=team.id, insights_json=json.dumps(insights, ensure_ascii=False))
                db.add(new_profile)
                db.commit()
                print(f"Created new profile for {team_name}.")
    finally:
        db.close()

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python process_teams.py <team_name> <raw_content_file>")
        sys.exit(1)
    
    team_name = sys.argv[1]
    content_file = sys.argv[2]
    
    with open(content_file, 'r', encoding='utf-8') as f:
        raw_content = f.read()
    
    process_team(team_name, raw_content)
