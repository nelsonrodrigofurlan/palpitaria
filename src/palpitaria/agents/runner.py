"""Runner task_based: ordem fixa do planner (sem LLM no ciclo ainda)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from palpitaria.agents.contracts import create_state, load_contracts
from palpitaria.agents.tools import call_tool
from palpitaria.agents.validator import validate_agent


def _hook(contratos: dict[str, Any], nome: str, mensagem: str) -> None:
    ganchos = (contratos.get("ganchos") or {}).get("ganchos") or {}
    acao = ganchos.get(nome, "log")
    prefix = "!" if acao == "alerta" else "-"
    print(f"  [{prefix}] {mensagem}", flush=True)


def _record(
    estado: dict[str, Any],
    nome: str,
    argumentos: dict[str, Any],
    resultado: dict[str, Any],
    *,
    ok: bool,
) -> None:
    estado["chamadas_ferramenta"] += 1
    estado["chamadas_por_ferramenta"][nome] = estado["chamadas_por_ferramenta"].get(nome, 0) + 1
    estado["historico"].append(
        {
            "ferramenta": nome,
            "argumentos": argumentos,
            "sucesso": ok,
            "resultado": resultado,
        }
    )
    estado["ultima_ferramenta"] = nome


def _check_limits(estado: dict[str, Any], nome: str) -> str | None:
    if estado["chamadas_ferramenta"] >= estado["max_chamadas_ferramenta"]:
        return "limite total de chamadas"
    lim = estado["limites_por_ferramenta"].get(nome)
    usados = estado["chamadas_por_ferramenta"].get(nome, 0)
    if lim is not None and usados >= lim:
        return f"limite de '{nome}' ({lim})"
    return None


def _ask_human(nome: str) -> bool:
    print(f"\n  [CONFIRMACAO HUMANA] '{nome}' requer autorizacao.")
    try:
        resp = input(f"  Autorizar '{nome}'? (s/n): ").strip().lower()
        return resp in {"s", "sim", "y", "yes"}
    except EOFError:
        print("  sem input — negando por seguranca")
        return False


def run_daily(
    agent_path: Path,
    *,
    competicoes: list[str] | None = None,
    modo: str | None = None,
    narrar: bool = True,
    persistir: bool = True,
    publicar: bool = False,
    canal: str = "local",
    aprovado_por: str = "",
    skip_sync: bool = False,
    skip_validate: bool = False,
) -> dict[str, Any]:
    agent_path = agent_path.resolve()
    if not skip_validate:
        result = validate_agent(agent_path)
        result.print_report(agent_path.name)
        if not result.ok:
            raise SystemExit(1)

    contracts = load_contracts(agent_path)
    comps = competicoes or ["BSA", "BSB"]
    estado = create_state(
        contracts,
        entrada="dia operacional",
        modo=modo,
        competicoes=comps,
    )
    inicio = time.time()

    print(f"\n=== Agente {agent_path.name} ({estado['tipo_agente']}) ===")
    print(f"Objetivo: {estado['objetivo']}")
    print(f"Competicoes: {', '.join(comps)}\n")

    sync_out: dict[str, Any] = {}
    analyze_out: dict[str, Any] = {
        "total": 0,
        "homologadas": [],
        "alternativas": [],
        "descartes": [],
        "sem_fundamento": [],
        "dia_label": "",
    }
    hist_out: dict[str, Any] = {}
    draft_out: dict[str, Any] = {}
    publish_out: dict[str, Any] | None = None

    # 1) sync
    if not skip_sync:
        estado["etapa"] += 1
        _hook(contracts, "antes_da_etapa", f"etapa {estado['etapa']}: sincronizar_competicoes")
        lim = _check_limits(estado, "sincronizar_competicoes")
        if lim:
            raise RuntimeError(lim)
        _hook(contracts, "antes_da_acao", "sincronizar_competicoes")
        try:
            sync_out = call_tool("sincronizar_competicoes", competicoes=comps, forcar=False)
            _record(estado, "sincronizar_competicoes", {"competicoes": comps}, sync_out, ok=True)
            _hook(contracts, "apos_acao", f"sync status={sync_out.get('status')}")
        except Exception as exc:  # noqa: BLE001
            _hook(contracts, "em_erro", str(exc))
            sync_out = {"status": "error", "erros": [str(exc)], "fixtures_por_comp": {}}
            _record(estado, "sincronizar_competicoes", {"competicoes": comps}, sync_out, ok=False)
    else:
        print("  [-] sync pulado (--skip-sync / rascunho)")

    # 2) analyze
    estado["etapa"] += 1
    _hook(contracts, "antes_da_etapa", f"etapa {estado['etapa']}: analisar_jogos_hoje")
    lim = _check_limits(estado, "analisar_jogos_hoje")
    if lim:
        raise RuntimeError(lim)
    _hook(contracts, "antes_da_acao", "analisar_jogos_hoje")
    analyze_out = call_tool(
        "analisar_jogos_hoje",
        competicoes=comps,
        limite=50,
        narrar=narrar,
        persistir=persistir,
    )
    _record(
        estado,
        "analisar_jogos_hoje",
        {"competicoes": comps, "narrar": narrar},
        {
            "total": analyze_out.get("total"),
            "homologadas": len(analyze_out.get("homologadas") or []),
            "sem_fundamento": len(analyze_out.get("sem_fundamento") or []),
        },
        ok=True,
    )
    _hook(
        contracts,
        "apos_acao",
        f"analises={analyze_out.get('total')} "
        f"homologadas={len(analyze_out.get('homologadas') or [])}",
    )

    # 3) historico IA (todas as comps do estado — unfiltered resolve)
    estado["etapa"] += 1
    _hook(contracts, "antes_da_etapa", f"etapa {estado['etapa']}: resolver_historico_ia")
    lim = _check_limits(estado, "resolver_historico_ia")
    if lim:
        raise RuntimeError(lim)
    hist_out = call_tool("resolver_historico_ia", competicao=None)
    _record(estado, "resolver_historico_ia", {"competicao": None}, hist_out, ok=True)
    _hook(
        contracts,
        "apos_acao",
        f"resolvidos={hist_out.get('resolvidos')} pendentes={hist_out.get('pendentes')}",
    )

    # 4) rascunho (obrigatorio)
    estado["etapa"] += 1
    dia_label = (
        analyze_out.get("dia_label")
        or sync_out.get("dia_label")
        or "hoje"
    )
    _hook(contracts, "antes_da_etapa", f"etapa {estado['etapa']}: rascunho_diario")
    draft_args = {
        "dia_label": dia_label,
        "resumo": {"sync_status": sync_out.get("status", "skipped")},
        "homologadas": analyze_out.get("homologadas") or [],
        "descartes": analyze_out.get("descartes") or [],
        "alternativas": analyze_out.get("alternativas") or [],
        "sem_fundamento": analyze_out.get("sem_fundamento") or [],
        "historico_ia": hist_out,
    }
    draft_out = call_tool("rascunho_diario", **draft_args)
    _record(estado, "rascunho_diario", {"dia_label": dia_label}, draft_out, ok=True)
    _hook(contracts, "apos_acao", "rascunho gerado")

    # 5) publicar (opcional + sensivel)
    if publicar:
        nome = "publicar_indicacoes"
        if nome in estado["acoes_sensiveis"] and not _ask_human(nome):
            publish_out = {
                "enviado": False,
                "referencia": "",
                "motivo": "confirmacao_humana_negada",
            }
        else:
            publish_out = call_tool(
                nome,
                canal=canal,
                texto=draft_out.get("texto", ""),
                aprovado_por=aprovado_por or "operador",
                confirmado=True,
            )
            _record(
                estado,
                nome,
                {"canal": canal, "aprovado_por": aprovado_por},
                publish_out,
                ok=bool(publish_out.get("enviado")),
            )

    # obrigatorias
    missing = [
        t
        for t in estado["ferramentas_obrigatorias"]
        if t not in estado["chamadas_por_ferramenta"]
        and not (t == "sincronizar_competicoes" and skip_sync)
    ]
    if missing:
        raise RuntimeError(f"ferramentas obrigatorias nao executadas: {missing}")

    artifact = {
        "dia_label": dia_label,
        "sync": sync_out,
        "analises": {
            "total": analyze_out.get("total", 0),
            "com_pick": len(analyze_out.get("homologadas") or [])
            + len(analyze_out.get("alternativas") or []),
            "sem_fundamento": len(analyze_out.get("sem_fundamento") or []),
            "descartadas": len(analyze_out.get("descartes") or []),
        },
        "homologadas": analyze_out.get("homologadas") or [],
        "descartes": (analyze_out.get("descartes") or [])
        + (analyze_out.get("sem_fundamento") or []),
        "historico_ia": hist_out,
        "rascunho_alerta": draft_out.get("texto", ""),
        "requer_aprovacao_humana": True,
        "publicacao": publish_out,
        "tempo_segundos": round(time.time() - inicio, 1),
        "etapas": estado["etapa"],
        "ferramentas_chamadas": dict(estado["chamadas_por_ferramenta"]),
    }
    estado["concluido"] = True
    estado["resultado"] = artifact

    print("\n--- Rascunho ---\n")
    print(draft_out.get("texto", ""))
    print(f"\n=== Fim ({artifact['tempo_segundos']}s) ===\n")
    return artifact
