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
    name: Mapped[str] = mapped_column(String(60), unique=True)
    slug: Mapped[str] = mapped_column(String(30), unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    commission_rate: Mapped[float] = mapped_column(Float, default=6.5)  # % de comissão (ex: 6.5)

    bets: Mapped[list["Bet"]] = relationship(back_populates="branch")


class Bet(Base):
    __tablename__ = "bets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id"), index=True)
    fixture_id: Mapped[int | None] = mapped_column(ForeignKey("fixtures.id"), nullable=True)
    description: Mapped[str] = mapped_column(String(200))  # ex: "EUA x Paraguai"
    odds: Mapped[float] = mapped_column(Float)
    stake: Mapped[float] = mapped_column(Float)
    outcome: Mapped[str] = mapped_column(String(20), default="PENDING")  # WIN, LOSS, PENDING
    profit_loss: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    branch: Mapped["Branch"] = relationship(back_populates="bets")
    fixture: Mapped["Fixture | None"] = relationship()
