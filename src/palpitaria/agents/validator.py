"""Valida contratos do agente: arquivos, YAML e consistência entre skills/toolbox/rules."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from palpitaria.agents.contracts import load_yaml_from_md

TIPOS_VALIDOS = frozenset({"task_based", "interactive", "goal_oriented", "autonomous"})

REQUIRED_FILES = (
    "agent.md",
    "rules.md",
    "skills.md",
    "hooks.md",
    "memory.md",
    "contracts/loop.md",
    "contracts/planner.md",
    "contracts/executor.md",
    "contracts/toolbox.md",
)


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def print_report(self, agent_name: str) -> None:
        print(f"\n{'=' * 60}")
        print(f"  Validando agente: {agent_name}")
        print(f"{'=' * 60}\n")
        for warning in self.warnings:
            print(f"  [AVISO] {warning}")
        for error in self.errors:
            print(f"  [ERRO] {error}")
        print(f"\n{'=' * 60}")
        if self.ok:
            print(f"  Resultado: VALIDO ({len(self.warnings)} avisos)")
        else:
            print(f"  Resultado: INVALIDO ({len(self.errors)} erros, {len(self.warnings)} avisos)")
        print(f"{'=' * 60}\n")


def validate_agent(agent_path: Path) -> ValidationResult:
    path = agent_path.resolve()
    contracts_dir = path / "contracts"
    errors: list[str] = []
    warnings: list[str] = []

    for relative in REQUIRED_FILES:
        file_path = path / relative
        if not file_path.exists():
            errors.append(f"{relative} nao encontrado")
            continue
        data = load_yaml_from_md(file_path)
        if not data:
            errors.append(f"{relative} existe mas nao contem YAML valido")

    habilidades = load_yaml_from_md(path / "skills.md")
    toolbox = load_yaml_from_md(contracts_dir / "toolbox.md")
    regras = load_yaml_from_md(path / "rules.md")
    agente = load_yaml_from_md(path / "agent.md")

    nomes_hab = {
        h["nome"] for h in habilidades.get("habilidades", []) if isinstance(h, dict) and "nome" in h
    }
    nomes_tb = {
        f["nome"] for f in toolbox.get("ferramentas", []) if isinstance(f, dict) and "nome" in f
    }

    for nome in nomes_tb - nomes_hab:
        errors.append(f"ferramenta '{nome}' esta no toolbox.md mas nao em skills.md")
    for nome in nomes_hab - nomes_tb:
        warnings.append(f"ferramenta '{nome}' esta em skills.md mas nao no toolbox.md")

    for nome in regras.get("ferramentas_obrigatorias", []) or []:
        if nome not in nomes_hab:
            errors.append(f"ferramenta obrigatoria '{nome}' nao existe em skills.md")

    chamadas = (regras.get("limites") or {}).get("chamadas_ferramenta") or {}
    if isinstance(chamadas, dict):
        for nome in chamadas:
            if nome != "total" and nome not in nomes_hab:
                warnings.append(f"limite definido para '{nome}' que nao existe em skills.md")

    for nome in regras.get("acoes_sensiveis") or []:
        if nome not in nomes_hab:
            errors.append(f"acao sensivel '{nome}' nao existe em skills.md")

    tipo = agente.get("tipo", "")
    if tipo and tipo not in TIPOS_VALIDOS:
        errors.append(f"tipo '{tipo}' invalido. Valores: {', '.join(sorted(TIPOS_VALIDOS))}")

    contrato_saida = agente.get("contrato_saida") or {}
    if not contrato_saida:
        warnings.append("agent.md nao define contrato_saida")
    elif not contrato_saida.get("campos_obrigatorios"):
        warnings.append("contrato_saida nao define campos_obrigatorios")

    return ValidationResult(ok=len(errors) == 0, errors=errors, warnings=warnings)
