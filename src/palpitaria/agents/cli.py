"""CLI: python -m palpitaria.agents validate|run|rascunho."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from palpitaria.agents import DEFAULT_AGENT, default_agent_path
from palpitaria.agents.runner import run_daily
from palpitaria.agents.validator import validate_agent


def _parse_comps(raw: str | None) -> list[str]:
    if not raw:
        return ["BSA", "BSB"]
    return [p.strip().upper() for p in raw.split(",") if p.strip()]


def _resolve_agent(path: str | None) -> Path:
    if not path:
        return default_agent_path(DEFAULT_AGENT)
    p = Path(path)
    if not p.is_absolute() and not p.exists():
        alt = default_agent_path(path)
        if alt.exists():
            return alt
    return p


def cmd_validar(args: argparse.Namespace) -> int:
    agent = _resolve_agent(args.agente)
    result = validate_agent(agent)
    # list OK files briefly
    for relative in (
        "agent.md",
        "rules.md",
        "skills.md",
        "hooks.md",
        "memory.md",
        "contracts/loop.md",
        "contracts/planner.md",
        "contracts/executor.md",
        "contracts/toolbox.md",
    ):
        fp = agent / relative
        if fp.exists():
            print(f"  [OK] {relative}")
    result.print_report(agent.name)
    return 0 if result.ok else 1


def cmd_rodar(args: argparse.Namespace) -> int:
    agent = _resolve_agent(args.agente)
    artifact = run_daily(
        agent,
        competicoes=_parse_comps(args.comps),
        modo=args.modo,
        narrar=not args.sem_narrar,
        persistir=not args.sem_persistir,
        publicar=args.publicar,
        canal=args.canal,
        aprovado_por=args.aprovado_por or "",
        skip_sync=args.skip_sync,
    )
    if args.json:
        print(json.dumps(artifact, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_rascunho(args: argparse.Namespace) -> int:
    agent = _resolve_agent(args.agente)
    artifact = run_daily(
        agent,
        competicoes=_parse_comps(args.comps),
        modo=args.modo or "task_based",
        narrar=not args.sem_narrar,
        persistir=not args.sem_persistir,
        skip_sync=True,
    )
    if args.json:
        print(json.dumps(artifact, ensure_ascii=False, indent=2, default=str))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m palpitaria.agents",
        description="Runtime minimo de agentes Palpitaria FC",
    )
    sub = parser.add_subparsers(dest="comando", required=True)

    p_val = sub.add_parser("validar", help="valida contratos do agente")
    p_val.add_argument("--agente", default=None, help="caminho ou nome (default: palpitaria-diario)")
    p_val.set_defaults(func=cmd_validar)

    p_run = sub.add_parser("rodar", help="ciclo diario: sync → analise → historico → rascunho")
    p_run.add_argument("--agente", default=None)
    p_run.add_argument("--comps", default="BSA,BSB")
    p_run.add_argument("--modo", default=None, choices=["task_based", "autonomous", "interactive", "goal_oriented"])
    p_run.add_argument("--sem-narrar", action="store_true", help="pula LLM (so modelo)")
    p_run.add_argument("--sem-persistir", action="store_true")
    p_run.add_argument("--skip-sync", action="store_true")
    p_run.add_argument("--publicar", action="store_true", help="tenta publicar (pede confirmacao)")
    p_run.add_argument("--canal", default="local")
    p_run.add_argument("--aprovado-por", default="")
    p_run.add_argument("--json", action="store_true")
    p_run.set_defaults(func=cmd_rodar)

    p_draft = sub.add_parser("rascunho", help="analisa + rascunho sem sync")
    p_draft.add_argument("--agente", default=None)
    p_draft.add_argument("--comps", default="BSA,BSB")
    p_draft.add_argument("--modo", default=None)
    p_draft.add_argument("--sem-narrar", action="store_true")
    p_draft.add_argument("--sem-persistir", action="store_true")
    p_draft.add_argument("--json", action="store_true")
    p_draft.set_defaults(func=cmd_rascunho)

    return parser


def main(argv: list[str] | None = None) -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
