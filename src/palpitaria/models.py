from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from palpitaria.database import Base


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    short_name: Mapped[str | None] = mapped_column(String(60), nullable=True)
    tla: Mapped[str | None] = mapped_column(String(5), nullable=True)
    crest_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    home_fixtures: Mapped[list["Fixture"]] = relationship(
        back_populates="home_team", foreign_keys="Fixture.home_team_id"
    )
    away_fixtures: Mapped[list["Fixture"]] = relationship(
        back_populates="away_team", foreign_keys="Fixture.away_team_id"
    )
    profiles: Mapped[list["TeamProfile"]] = relationship(back_populates="team")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    total_deposits: Mapped[float] = mapped_column(Float, default=0.0)
    total_withdrawals: Mapped[float] = mapped_column(Float, default=0.0)
    favorite_comp_code: Mapped[str | None] = mapped_column(String(10), nullable=True)

    branches: Mapped[list["Branch"]] = relationship(back_populates="user")
    insights: Mapped[list["UserInsight"]] = relationship(back_populates="user")


class Fixture(Base):
    __tablename__ = "fixtures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    competition_code: Mapped[str] = mapped_column(String(10), index=True)
    season: Mapped[int] = mapped_column(Integer)
    matchday: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stage: Mapped[str | None] = mapped_column(String(40), nullable=True)
    group_name: Mapped[str | None] = mapped_column(String(20), nullable=True)
    utc_date: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="SCHEDULED")
    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    home_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    venue_stadium: Mapped[str | None] = mapped_column(String(120), nullable=True)
    venue_city: Mapped[str | None] = mapped_column(String(80), nullable=True)
    venue_state: Mapped[str | None] = mapped_column(String(40), nullable=True)

    home_team: Mapped["Team"] = relationship(
        back_populates="home_fixtures", foreign_keys=[home_team_id]
    )
    away_team: Mapped["Team"] = relationship(
        back_populates="away_fixtures", foreign_keys=[away_team_id]
    )
    picks: Mapped[list["Pick"]] = relationship(back_populates="fixture")
    report: Mapped["FixtureReport | None"] = relationship(
        back_populates="fixture", uselist=False
    )


class FixtureReport(Base):
    __tablename__ = "fixture_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), unique=True, index=True)
    excluded: Mapped[bool] = mapped_column(default=True)
    exclusion_reasons_json: Mapped[str] = mapped_column(Text, default="[]")
    criteria_json: Mapped[str] = mapped_column(Text, default="[]")
    goal_potential_score: Mapped[float] = mapped_column(Float, default=0.0)
    llm_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    best_pick_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    match_context_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    analyzed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    fixture: Mapped["Fixture"] = relationship(back_populates="report")


class AiRecommendation(Base):
    """Snapshot imutável da recomendação da IA — resolvido após o jogo terminar."""

    __tablename__ = "ai_recommendations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), index=True)
    competition_code: Mapped[str] = mapped_column(String(10), index=True)
    match_label: Mapped[str] = mapped_column(String(200))  # ex: Portugal x RD Congo
    analyzed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    market: Mapped[str] = mapped_column(String(80))
    verdict: Mapped[str] = mapped_column(String(20), default="CANDIDATE")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope: Mapped[str] = mapped_column(String(20), default="goals")  # goals | alternate
    excluded: Mapped[bool] = mapped_column(default=False)
    goal_potential_score: Mapped[float] = mapped_column(Float, default=0.0)
    outcome: Mapped[str] = mapped_column(String(10), default="PENDING")  # PENDING, HIT, MISS, VOID
    final_home_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    final_away_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    fixture: Mapped["Fixture"] = relationship()


class TeamProfile(Base):
    __tablename__ = "team_profiles"
    __table_args__ = (UniqueConstraint("team_id", "computed_at", name="uq_team_profile_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    matches_sampled: Mapped[int] = mapped_column(Integer, default=0)
    avg_goals_scored: Mapped[float] = mapped_column(Float, default=0.0)
    avg_goals_conceded: Mapped[float] = mapped_column(Float, default=0.0)
    zero_zero_rate: Mapped[float] = mapped_column(Float, default=0.0)
    over_05_rate: Mapped[float] = mapped_column(Float, default=0.0)
    over_15_rate: Mapped[float] = mapped_column(Float, default=0.0)
    over_25_rate: Mapped[float] = mapped_column(Float, default=0.0)
    win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    both_teams_score_rate: Mapped[float] = mapped_column(Float, default=0.0)
    insights_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    team: Mapped["Team"] = relationship(back_populates="profiles")


class Pick(Base):
    __tablename__ = "picks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), index=True)
    branch: Mapped[str] = mapped_column(String(30), index=True)
    verdict: Mapped[str] = mapped_column(String(20))
    pessimistic: Mapped[str] = mapped_column(Text)
    realistic: Mapped[str] = mapped_column(Text)
    optimistic: Mapped[str] = mapped_column(Text)
    criteria_json: Mapped[str] = mapped_column(Text)
    llm_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    goal_potential_score: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    outcome: Mapped[str | None] = mapped_column(String(10), nullable=True)

    fixture: Mapped["Fixture"] = relationship(back_populates="picks")


class Branch(Base):
    __tablename__ = "branches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    slug: Mapped[str] = mapped_column(String(60), unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    commission_rate: Mapped[float] = mapped_column(Float, default=6.5)  # % de comissão (ex: 6.5)
    side: Mapped[str] = mapped_column(String(10), default="BACK")  # BACK | LAY — define P&L da filial

    user: Mapped["User | None"] = relationship(back_populates="branches")
    bets: Mapped[list["Bet"]] = relationship(back_populates="branch")
    monthly_summaries: Mapped[list["BranchMonthlySummary"]] = relationship(back_populates="branch")


class Bet(Base):
    __tablename__ = "bets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id"), index=True)
    fixture_id: Mapped[int | None] = mapped_column(ForeignKey("fixtures.id"), nullable=True)
    competition_code: Mapped[str | None] = mapped_column(String(10), index=True)
    description: Mapped[str] = mapped_column(String(200))  # ex: "EUA x Paraguai"
    odds: Mapped[float] = mapped_column(Float)
    stake: Mapped[float] = mapped_column(Float)
    outcome: Mapped[str] = mapped_column(String(20), default="PENDING")  # WIN, LOSS, PENDING
    profit_loss: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    branch: Mapped["Branch"] = relationship(back_populates="bets")
    fixture: Mapped["Fixture | None"] = relationship()


class BranchMonthlySummary(Base):
    """Consolidado mensal por filial — gerado ao virar o mês."""

    __tablename__ = "branch_monthly_summaries"
    __table_args__ = (UniqueConstraint("branch_id", "year", "month", "competition_code", name="uq_branch_month_comp"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id"), index=True)
    year: Mapped[int] = mapped_column(Integer, index=True)
    month: Mapped[int] = mapped_column(Integer, index=True)
    competition_code: Mapped[str] = mapped_column(String(10), default="WC", index=True)
    bet_count: Mapped[int] = mapped_column(Integer, default=0)
    win_count: Mapped[int] = mapped_column(Integer, default=0)
    loss_count: Mapped[int] = mapped_column(Integer, default=0)
    pending_count: Mapped[int] = mapped_column(Integer, default=0)
    total_pl: Mapped[float] = mapped_column(Float, default=0.0)
    total_stake: Mapped[float] = mapped_column(Float, default=0.0)
    commission_rate: Mapped[float] = mapped_column(Float, default=6.5)
    closed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    branch: Mapped["Branch"] = relationship(back_populates="monthly_summaries")


class UserInsight(Base):
    """Percepções do usuário avaliadas pela IA para compor a base de conhecimento."""

    __tablename__ = "user_insights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    content: Mapped[str] = mapped_column(Text)  # O que o usuário disse
    evaluation: Mapped[str | None] = mapped_column(Text, nullable=True)  # Análise da IA
    is_valid: Mapped[bool] = mapped_column(default=False)  # Se passou pelo crivo dos pilares
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User | None"] = relationship(back_populates="insights")
    team: Mapped["Team | None"] = relationship()


class Competition(Base):
    __tablename__ = "competitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    season: Mapped[int] = mapped_column(Integer, default=2026)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    odds_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # Cache de odds diário


class ApiConfig(Base):
    __tablename__ = "api_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    value: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class LlmUsageLog(Base):
    """Registro local de chamadas LLM — custo e tokens por operação."""

    __tablename__ = "llm_usage_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(30), default="openrouter", index=True)
    model: Mapped[str] = mapped_column(String(120))
    feature: Mapped[str] = mapped_column(String(40), index=True)  # explainer, scraper, wc_profile, chat
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    generation_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    success: Mapped[bool] = mapped_column(default=True)
    error_message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class PipelineRun(Base):
    """Execução do pipeline — trava global de 1x por dia (web ou remoto)."""

    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_day: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD (APP_TIMEZONE)
    trigger: Mapped[str] = mapped_column(String(20), index=True)  # remote_api | web_admin
    status: Mapped[str] = mapped_column(String(20), default="running")  # running | done | error
    comp_code: Mapped[str] = mapped_column(String(10), default="WC")
    watch_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class RemotePipelineDaily(Base):
    """Trava atômica: no máximo 1 pipeline completo por dia e por campeonato."""

    __tablename__ = "remote_pipeline_daily"

    run_day: Mapped[str] = mapped_column(String(10), primary_key=True)
    comp_code: Mapped[str] = mapped_column(String(10), primary_key=True, default="WC")
    pipeline_run_id: Mapped[int] = mapped_column(ForeignKey("pipeline_runs.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PipelineLogLine(Base):
    __tablename__ = "pipeline_log_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("pipeline_runs.id"), index=True)
    line: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Cycle(Base):
    __tablename__ = "cycles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(60))  # ex: "Ciclo 1"
    initial_stake: Mapped[float] = mapped_column(Float)
    target_amount: Mapped[float] = mapped_column(Float)
    current_amount: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")  # ACTIVE, COMPLETED, FAILED
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship()
    steps: Mapped[list["CycleStep"]] = relationship(back_populates="cycle", cascade="all, delete-orphan")


class CycleStep(Base):
    __tablename__ = "cycle_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cycle_id: Mapped[int] = mapped_column(ForeignKey("cycles.id"), index=True)
    fixture_id: Mapped[int | None] = mapped_column(ForeignKey("fixtures.id"), nullable=True)
    description: Mapped[str] = mapped_column(String(200))
    stake: Mapped[float] = mapped_column(Float)
    target_profit_pct: Mapped[float] = mapped_column(Float, default=5.0)
    actual_profit_loss: Mapped[float] = mapped_column(Float, default=0.0)
    outcome: Mapped[str] = mapped_column(String(20), default="PENDING")  # WIN, LOSS, PENDING
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    cycle: Mapped["Cycle"] = relationship(back_populates="steps")
    fixture: Mapped["Fixture | None"] = relationship()
