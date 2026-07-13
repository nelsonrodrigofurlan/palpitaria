"""Carrega contratos .md (bloco YAML) e monta estado inicial."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


def load_yaml_from_md(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    match = re.search(r"```yaml\n(.*?)```", text, re.DOTALL)
    if not match:
        return {}
    try:
        return yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML invalido em {path}: {exc}") from exc


def load_contracts(agent_path: Path) -> dict[str, Any]:
    contracts_dir = agent_path / "contracts"
    return {
        "agente": load_yaml_from_md(agent_path / "agent.md"),
        "ciclo": load_yaml_from_md(contracts_dir / "loop.md"),
        "planejador": load_yaml_from_md(contracts_dir / "planner.md"),
        "caixa_ferramentas": load_yaml_from_md(contracts_dir / "toolbox.md"),
        "executor": load_yaml_from_md(contracts_dir / "executor.md"),
        "regras": load_yaml_from_md(agent_path / "rules.md"),
        "ganchos": load_yaml_from_md(agent_path / "hooks.md"),
        "habilidades": load_yaml_from_md(agent_path / "skills.md"),
        "memoria": load_yaml_from_md(agent_path / "memory.md"),
        "comandos": load_yaml_from_md(agent_path / "commands.md"),
    }


def create_state(
    contracts: dict[str, Any],
    *,
    entrada: str = "",
    modo: str | None = None,
    competicoes: list[str] | None = None,
) -> dict[str, Any]:
    regras = contracts.get("regras") or {}
    ciclo = contracts.get("ciclo") or {}
    agente = contracts.get("agente") or {}
    limites = regras.get("limites") or {}
    chamadas = limites.get("chamadas_ferramenta") or {}

    if isinstance(chamadas, dict):
        max_total = int(chamadas.get("total", 10))
        por_ferramenta = {
            k: int(v) for k, v in chamadas.items() if k != "total"
        }
    else:
        max_total = int(chamadas or 10)
        por_ferramenta = {}

    return {
        "objetivo": ciclo.get("objetivo") or agente.get("objetivo") or "desconhecido",
        "entrada": entrada,
        "tipo_agente": modo or agente.get("tipo") or "task_based",
        "competicoes": competicoes or ["BSA", "BSB"],
        "etapa": 0,
        "chamadas_ferramenta": 0,
        "chamadas_por_ferramenta": {},
        "max_etapas": int(limites.get("max_etapas", 12)),
        "max_chamadas_ferramenta": max_total,
        "limites_por_ferramenta": por_ferramenta,
        "sem_progresso": int(limites.get("sem_progresso", 3)),
        "limite_tempo_segundos": int(limites.get("limite_tempo_segundos", 600)),
        "acoes_sensiveis": list(regras.get("acoes_sensiveis") or []),
        "ferramentas_obrigatorias": list(regras.get("ferramentas_obrigatorias") or []),
        "historico": [],
        "concluido": False,
        "resultado": None,
        "etapas_sem_progresso": 0,
        "ultima_ferramenta": None,
    }
