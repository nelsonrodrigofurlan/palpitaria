"""Runtime mínimo de agentes Palpitaria FC (contratos + tools + run scriptado)."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_AGENTS_DIR = REPO_ROOT / "agents"
DEFAULT_AGENT = "palpitaria-diario"


def default_agent_path(name: str = DEFAULT_AGENT) -> Path:
    return DEFAULT_AGENTS_DIR / name
