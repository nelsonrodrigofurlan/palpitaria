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
from palpitaria.models import Team, Fixture, TeamProfile, Branch, User, UserInsight, Competition, ApiConfig
from palpitaria.config import settings
from palpitaria.services.auth import get_password_hash

def migrate_to_supabase():
    print(f"Target Database: {settings.db_url}")
    if not settings.uses_postgres:
        print("ERROR: DATABASE_URL deve apontar para Supabase (PostgreSQL). Atualize o .env.")
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
            conn.execute(text(
                "ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS venue_stadium VARCHAR(120)"
            ))
            conn.execute(text(
                "ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS venue_city VARCHAR(80)"
            ))
            conn.execute(text(
                "ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS venue_state VARCHAR(40)"
            ))
            # New columns for Auth
            conn.execute(text(
                "ALTER TABLE branches ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id)"
            ))
            conn.execute(text(
                "ALTER TABLE user_insights ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id)"
            ))
            conn.execute(text(
                "ALTER TABLE bets ADD COLUMN IF NOT EXISTS competition_code VARCHAR(10)"
            ))
            conn.execute(text(
                "ALTER TABLE branch_monthly_summaries ADD COLUMN IF NOT EXISTS competition_code VARCHAR(10) DEFAULT 'WC'"
            ))
            # Drop old constraint if exists and add new one
            conn.execute(text(
                "ALTER TABLE branch_monthly_summaries DROP CONSTRAINT IF EXISTS uq_branch_month"
            ))
            
            conn.execute(text(
                "ALTER TABLE competitions ADD COLUMN IF NOT EXISTS season INTEGER DEFAULT 2026"
            ))

        # 0. Seed Users
        print("Seeding users...")
        users_to_seed = [
            {
                "email": "nelson.r.furlan@gmail.com",
                "full_name": "Nelson Furlan",
                "password": "Palpitaria@2026!"
            },
            {
                "email": "danilo.furlan@gmail.com",
                "full_name": "Danilo Furlan",
                "password": "Danilo#Secure@2026"
            },
            {
                "email": "welligton.oliveira@gmail.com",
                "full_name": "Welligton Oliveira",
                "password": "Welligton!Strong#2026"
            }
        ]

        for u_data in users_to_seed:
            user = db.query(User).filter_by(email=u_data["email"]).first()
            if not user:
                hashed = get_password_hash(u_data["password"])
                user = User(
                    email=u_data["email"],
                    hashed_password=hashed,
                    full_name=u_data["full_name"],
                    is_active=True
                )
                db.add(user)
                db.flush()
                print(f"User {u_data['email']} created.")
            else:
                print(f"User {u_data['email']} already exists.")

        nelson = db.query(User).filter_by(email="nelson.r.furlan@gmail.com").first()

        # 1. Create Default Branches and link to Nelson
        print("Seeding default branches...")
        branches = [
            {"name": "Over 0.5 Goals", "slug": "over_0_5", "description": "Mercado de pelo menos 1 gol", "user_id": nelson.id},
            {"name": "Over 1.5 Goals", "slug": "over_1_5", "description": "Mercado de pelo menos 2 gols", "user_id": nelson.id},
            {"name": "1X2 (Match Odds)", "slug": "1x2", "description": "Vitória, Empate ou Derrota", "user_id": nelson.id},
        ]
        for b_data in branches:
            exists = db.query(Branch).filter_by(slug=b_data["slug"]).first()
            if not exists:
                db.add(Branch(**b_data))
            elif exists.user_id is None:
                exists.user_id = nelson.id
        
        # Link existing insights to Nelson if they don't have a user
        db.query(UserInsight).filter(UserInsight.user_id == None).update({UserInsight.user_id: nelson.id})

        # 2. Seed Competitions
        print("Seeding competitions...")
        comps = [
            {"code": "WC", "name": "Copa do Mundo 2026", "is_active": True},
            {"code": "BSA", "name": "Brasileirão Série A", "is_active": True},
            {"code": "CDB", "name": "Copa do Brasil", "is_active": True},
        ]
        for c_data in comps:
            exists = db.query(Competition).filter_by(code=c_data["code"]).first()
            if not exists:
                db.add(Competition(**c_data))

        # 3. Seed API Configs (non-sensitive only)
        print("Seeding API configs...")
        configs = [
            {"key": "OPENAI_BASE_URL", "value": settings.openai_base_url, "description": "URL base para LLM (ex: OpenRouter)"},
        ]
        for cfg in configs:
            exists = db.query(ApiConfig).filter_by(key=cfg["key"]).first()
            if not exists:
                db.add(ApiConfig(**cfg))
        
        db.commit()
        print("Migration and seeding complete!")

    except Exception as e:
        print(f"Error during migration: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    migrate_to_supabase()
