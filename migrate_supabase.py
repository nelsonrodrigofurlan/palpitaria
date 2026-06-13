import os
import json
from datetime import datetime
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import sys
from pathlib import Path

# Add src to path
sys.path.append(str(Path(__file__).parent / "src"))

from palpitaria.database import Base
from palpitaria.models import Team, Fixture, TeamProfile, Branch
from palpitaria.config import settings

def migrate_to_supabase():
    print(f"Target Database: {settings.db_url}")
    if "sqlite" in settings.db_url:
        print("ERROR: DATABASE_URL is still pointing to SQLite. Please update your .env file.")
        return

    engine = create_engine(settings.db_url)
    Session = sessionmaker(bind=engine)
    db = Session()

    try:
        print("Creating tables if they don't exist...")
        Base.metadata.create_all(bind=engine)

        print("Applying column migrations...")
        with engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE fixture_reports ADD COLUMN IF NOT EXISTS best_pick_json TEXT"
            ))
            conn.execute(text(
                "ALTER TABLE fixture_reports ADD COLUMN IF NOT EXISTS match_context_json TEXT"
            ))

        # 1. Create Default Branches
        print("Seeding default branches...")
        branches = [
            {"name": "Over 0.5 Goals", "slug": "over_0_5", "description": "Mercado de pelo menos 1 gol"},
            {"name": "Over 1.5 Goals", "slug": "over_1_5", "description": "Mercado de pelo menos 2 gols"},
            {"name": "1X2 (Match Odds)", "slug": "1x2", "description": "Vitória, Empate ou Derrota"},
        ]
        for b_data in branches:
            exists = db.query(Branch).filter_by(slug=b_data["slug"]).first()
            if not exists:
                db.add(Branch(**b_data))
        
        db.commit()
        print("Migration and seeding complete!")

    except Exception as e:
        print(f"Error during migration: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    migrate_to_supabase()
