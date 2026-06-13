-- Script de criação de tabelas para Supabase (PostgreSQL)
-- Projeto: Palpitaria FC

-- Extensões necessárias
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 1. Tabela de Times
CREATE TABLE IF NOT EXISTS public.teams (
    id SERIAL PRIMARY KEY,
    external_id INTEGER UNIQUE NOT NULL,
    name TEXT NOT NULL,
    short_name TEXT,
    tla TEXT,
    crest_url TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Tabela de Jogos (Fixtures)
CREATE TABLE IF NOT EXISTS public.fixtures (
    id SERIAL PRIMARY KEY,
    external_id INTEGER UNIQUE NOT NULL,
    competition_code TEXT NOT NULL,
    season INTEGER NOT NULL,
    matchday INTEGER,
    stage TEXT,
    group_name TEXT,
    utc_date TIMESTAMPTZ NOT NULL,
    status TEXT DEFAULT 'SCHEDULED',
    home_team_id INTEGER REFERENCES public.teams(id),
    away_team_id INTEGER REFERENCES public.teams(id),
    home_score INTEGER,
    away_score INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 3. Tabela de Perfis de Times (Team Profiles)
CREATE TABLE IF NOT EXISTS public.team_profiles (
    id SERIAL PRIMARY KEY,
    team_id INTEGER REFERENCES public.teams(id) NOT NULL,
    computed_at TIMESTAMPTZ DEFAULT NOW(),
    matches_sampled INTEGER DEFAULT 0,
    avg_goals_scored FLOAT DEFAULT 0.0,
    avg_goals_conceded FLOAT DEFAULT 0.0,
    zero_zero_rate FLOAT DEFAULT 0.0,
    over_05_rate FLOAT DEFAULT 0.0,
    over_15_rate FLOAT DEFAULT 0.0,
    over_25_rate FLOAT DEFAULT 0.0,
    win_rate FLOAT DEFAULT 0.0,
    both_teams_score_rate FLOAT DEFAULT 0.0,
    insights_json TEXT,
    raw_json TEXT,
    UNIQUE(team_id, computed_at)
);

-- 4. Tabela de Relatórios de Jogos (Fixture Reports)
CREATE TABLE IF NOT EXISTS public.fixture_reports (
    id SERIAL PRIMARY KEY,
    fixture_id INTEGER UNIQUE REFERENCES public.fixtures(id) NOT NULL,
    excluded BOOLEAN DEFAULT TRUE,
    exclusion_reasons_json TEXT DEFAULT '[]',
    criteria_json TEXT DEFAULT '[]',
    goal_potential_score FLOAT DEFAULT 0.0,
    llm_explanation TEXT,
    analyzed_at TIMESTAMPTZ DEFAULT NOW()
);

-- 5. Tabela de Palpites (Picks)
CREATE TABLE IF NOT EXISTS public.picks (
    id SERIAL PRIMARY KEY,
    fixture_id INTEGER REFERENCES public.fixtures(id) NOT NULL,
    branch TEXT NOT NULL,
    verdict TEXT NOT NULL,
    pessimistic TEXT,
    realistic TEXT,
    optimistic TEXT,
    criteria_json TEXT,
    llm_explanation TEXT,
    goal_potential_score FLOAT DEFAULT 0.0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    outcome TEXT
);

-- 6. Tabela de Filiais (Branches)
CREATE TABLE IF NOT EXISTS public.branches (
    id SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 7. Tabela de Apostas (Bets)
CREATE TABLE IF NOT EXISTS public.bets (
    id SERIAL PRIMARY KEY,
    branch_id INTEGER REFERENCES public.branches(id) NOT NULL,
    fixture_id INTEGER REFERENCES public.fixtures(id),
    description TEXT NOT NULL,
    odds FLOAT NOT NULL,
    stake FLOAT NOT NULL,
    outcome TEXT DEFAULT 'PENDING',
    profit_loss FLOAT DEFAULT 0.0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Habilitar RLS (Row Level Security) - Opcional por enquanto, mas boa prática no Supabase
ALTER TABLE public.teams ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.fixtures ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.team_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.fixture_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.picks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.branches ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.bets ENABLE ROW LEVEL SECURITY;

-- Políticas simples para permitir acesso total (ajustar depois se necessário)
CREATE POLICY "Allow all for authenticated" ON public.teams FOR ALL TO authenticated USING (true);
CREATE POLICY "Allow all for authenticated" ON public.fixtures FOR ALL TO authenticated USING (true);
CREATE POLICY "Allow all for authenticated" ON public.team_profiles FOR ALL TO authenticated USING (true);
CREATE POLICY "Allow all for authenticated" ON public.fixture_reports FOR ALL TO authenticated USING (true);
CREATE POLICY "Allow all for authenticated" ON public.picks FOR ALL TO authenticated USING (true);
CREATE POLICY "Allow all for authenticated" ON public.branches FOR ALL TO authenticated USING (true);
CREATE POLICY "Allow all for authenticated" ON public.bets FOR ALL TO authenticated USING (true);
