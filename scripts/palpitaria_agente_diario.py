#!/usr/bin/env python3
"""Atalho da área de trabalho — roda o agente diário BSA/BSB (local, rascunho)."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = Path.home() / ".palpitaria" / "agent_diario.json"
LOG_DIR = Path.home() / ".palpitaria" / "logs"


def _configure_stdio() -> None:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def load_config() -> dict:
    defaults = {
        "comps": "BSA,BSB",
        "planejador": "llm",
        "sem_narrar": False,
        "skip_sync": False,
    }
    if not CONFIG_PATH.exists():
        return defaults
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return defaults
    defaults.update({k: v for k, v in data.items() if v is not None})
    return defaults


def main() -> int:
    _configure_stdio()
    sys.path.insert(0, str(REPO_ROOT / "src"))

    cfg = load_config()
    comps = [c.strip().upper() for c in str(cfg.get("comps", "BSA,BSB")).split(",") if c.strip()]
    planejador = str(cfg.get("planejador") or "llm")
    narrar = not bool(cfg.get("sem_narrar"))
    skip_sync = bool(cfg.get("skip_sync"))

    now = datetime.now(ZoneInfo("America/Sao_Paulo"))
    print("=" * 60)
    print("Palpitaria FC — Agente Diário (local)")
    print(f"Horário SP: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Competições: {', '.join(comps)}")
    print(f"Planejador: {planejador}")
    print("Publicação: NÃO (só rascunho — você aprova depois)")
    print("=" * 60)
    print()

    from palpitaria.agents import default_agent_path
    from palpitaria.agents.runner import run_daily

    agent = default_agent_path()
    if not agent.exists():
        print(f"Agente não encontrado: {agent}")
        input("Pressione Enter para fechar...")
        return 1

    try:
        artifact = run_daily(
            agent,
            competicoes=comps,
            modo="autonomous",
            narrar=narrar,
            persistir=True,
            publicar=False,
            skip_sync=skip_sync,
            planejador=planejador,
        )
    except SystemExit as exc:
        code = int(exc.code or 1) if isinstance(exc.code, int) else 1
        input("Pressione Enter para fechar...")
        return code
    except Exception as exc:
        print(f"\nERRO: {exc}")
        input("Pressione Enter para fechar...")
        return 1

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"agente_diario_{stamp}.txt"
    draft = artifact.get("rascunho_alerta") or "(sem rascunho)"
    log_path.write_text(
        f"dia={artifact.get('dia_label')}\n"
        f"parada={artifact.get('motivo_parada')}\n"
        f"ferramentas={artifact.get('ferramentas_chamadas')}\n\n"
        f"{draft}\n",
        encoding="utf-8",
    )
    print(f"\nRascunho também salvo em:\n  {log_path}")
    print("\nPronto. Revise o rascunho acima antes de qualquer envio.")
    input("Pressione Enter para fechar...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
