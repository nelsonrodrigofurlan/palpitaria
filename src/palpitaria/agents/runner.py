"""Ciclo do agente: perceber → planejar → agir → avaliar."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from palpitaria.agents.contracts import create_state, load_contracts
from palpitaria.agents.planner import (
    TOKENS_ZERO,
    _tool_names,
    decide,
    perceber,
    validate_plan,
)
from palpitaria.agents.tools import call_tool
from palpitaria.agents.validator import validate_agent


def _hook(contratos: dict[str, Any], nome: str, mensagem: str) -> None:
    ganchos = (contratos.get("ganchos") or {}).get("ganchos") or {}
    acao = ganchos.get(nome, "log")
    prefix = "!" if acao == "alerta" else "-"
    print(f"  [{prefix}] {mensagem}", flush=True)


def _ask_human_tool(nome: str) -> bool:
    print(f"\n  [CONFIRMACAO HUMANA] '{nome}' requer autorizacao.")
    try:
        resp = input(f"  Autorizar '{nome}'? (s/n): ").strip().lower()
        return resp in {"s", "sim", "y", "yes"}
    except EOFError:
        print("  sem input — negando por seguranca")
        return False


def _ask_user(pergunta: str) -> str:
    print(f"\n  [PERGUNTA] {pergunta}")
    try:
        return input("  > ").strip()
    except EOFError:
        return ""


def _check_limits(estado: dict[str, Any], nome: str) -> str | None:
    if estado["chamadas_ferramenta"] >= estado["max_chamadas_ferramenta"]:
        return "limite total de chamadas"
    lim = estado["limites_por_ferramenta"].get(nome)
    usados = estado["chamadas_por_ferramenta"].get(nome, 0)
    if lim is not None and usados >= lim:
        return f"limite de '{nome}' ({lim})"
    return None


def _touch_progress(estado: dict[str, Any], nome: str | None) -> bool:
    """True se estagnou."""
    if nome and nome == estado.get("ultima_ferramenta"):
        estado["etapas_sem_progresso"] += 1
    else:
        estado["etapas_sem_progresso"] = 0
    estado["ultima_ferramenta"] = nome
    return estado["etapas_sem_progresso"] >= estado.get("sem_progresso", 3)


def _add_tokens(estado: dict[str, Any], uso: dict[str, int]) -> None:
    for k in ("prompt", "completion", "total"):
        estado["tokens_consumidos"][k] += int(uso.get(k, 0) or 0)


def _enrich_args(
    nome: str,
    args: dict[str, Any] | None,
    estado: dict[str, Any],
) -> dict[str, Any]:
    """Mescla argumentos da LLM com defaults do runtime / resultados previos."""
    ops = estado.get("opcoes") or {}
    comps = list(estado.get("competicoes") or ["BSA", "BSB"])
    ctx = estado.get("contexto") or {}
    merged: dict[str, Any] = dict(args or {})

    if nome == "sincronizar_competicoes":
        merged.setdefault("competicoes", comps)
        merged.setdefault("forcar", False)
    elif nome == "analisar_jogos_hoje":
        merged.setdefault("competicoes", comps)
        merged.setdefault("limite", 50)
        merged.setdefault("narrar", ops.get("narrar", True))
        merged.setdefault("persistir", ops.get("persistir", True))
    elif nome == "resolver_historico_ia":
        # None = todas; LLM pode passar uma comp especifica
        if "competicao" not in merged:
            merged["competicao"] = None
        elif merged["competicao"] in ("", "todas", "all", "*"):
            merged["competicao"] = None
    elif nome == "rascunho_diario":
        analises = ctx.get("analises") or {}
        sync = ctx.get("sync") or {}
        hist = ctx.get("historico_ia") or {}
        merged.setdefault(
            "dia_label",
            analises.get("dia_label") or sync.get("dia_label") or "hoje",
        )
        merged.setdefault("resumo", {"sync_status": sync.get("status", "skipped")})
        merged.setdefault("homologadas", analises.get("homologadas") or [])
        merged.setdefault("descartes", analises.get("descartes") or [])
        merged.setdefault("alternativas", analises.get("alternativas") or [])
        merged.setdefault("sem_fundamento", analises.get("sem_fundamento") or [])
        merged.setdefault("historico_ia", hist)
    elif nome == "publicar_indicacoes":
        draft = ctx.get("rascunho") or {}
        merged.setdefault("canal", ops.get("canal") or "local")
        merged.setdefault("texto", draft.get("texto") or "")
        merged.setdefault("aprovado_por", ops.get("aprovado_por") or "operador")
        merged["confirmado"] = True  # so chega aqui apos gate humano

    return merged


def _store_result(estado: dict[str, Any], nome: str, resultado: dict[str, Any]) -> None:
    ctx = estado.setdefault("contexto", {})
    if nome == "sincronizar_competicoes":
        ctx["sync"] = resultado
    elif nome == "analisar_jogos_hoje":
        ctx["analises"] = resultado
    elif nome == "resolver_historico_ia":
        ctx["historico_ia"] = resultado
    elif nome == "rascunho_diario":
        ctx["rascunho"] = resultado
    elif nome == "publicar_indicacoes":
        ctx["publicacao"] = resultado


def _record_tool(
    estado: dict[str, Any],
    *,
    plano: dict[str, Any],
    nome: str,
    argumentos: dict[str, Any],
    resultado: dict[str, Any],
    ok: bool,
) -> None:
    estado["chamadas_ferramenta"] += 1
    estado["chamadas_por_ferramenta"][nome] = estado["chamadas_por_ferramenta"].get(nome, 0) + 1
    estado["historico"].append(
        {
            "etapa": estado["etapa"],
            "plano": plano,
            "ferramenta": nome,
            "argumentos": argumentos,
            "sucesso": ok,
            "resultado": resultado,
        }
    )


def _obrigatorias_ok(estado: dict[str, Any]) -> list[str]:
    skip = bool((estado.get("opcoes") or {}).get("skip_sync"))
    usadas = estado.get("chamadas_por_ferramenta") or {}
    return [
        t
        for t in (estado.get("ferramentas_obrigatorias") or [])
        if t not in usadas and not (t == "sincronizar_competicoes" and skip)
    ]


def _build_artifact(estado: dict[str, Any], inicio: float) -> dict[str, Any]:
    ctx = estado.get("contexto") or {}
    analises = ctx.get("analises") or {}
    sync = ctx.get("sync") or {}
    hist = ctx.get("historico_ia") or {}
    draft = ctx.get("rascunho") or {}
    return {
        "dia_label": draft.get("dia_label")
        or analises.get("dia_label")
        or sync.get("dia_label")
        or "hoje",
        "sync": sync,
        "analises": {
            "total": analises.get("total", 0),
            "com_pick": len(analises.get("homologadas") or [])
            + len(analises.get("alternativas") or []),
            "sem_fundamento": len(analises.get("sem_fundamento") or []),
            "descartadas": len(analises.get("descartes") or []),
        },
        "homologadas": analises.get("homologadas") or [],
        "descartes": (analises.get("descartes") or [])
        + (analises.get("sem_fundamento") or []),
        "historico_ia": hist,
        "rascunho_alerta": draft.get("texto", ""),
        "requer_aprovacao_humana": True,
        "publicacao": ctx.get("publicacao"),
        "tempo_segundos": round(time.time() - inicio, 1),
        "etapas": estado["etapa"],
        "ferramentas_chamadas": dict(estado.get("chamadas_por_ferramenta") or {}),
        "tokens_planejador": dict(estado.get("tokens_consumidos") or TOKENS_ZERO),
        "criterio_final": estado.get("criterio_final"),
        "planejador": (estado.get("opcoes") or {}).get("planejador"),
    }


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
    planejador: str = "llm",
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
    estado["opcoes"] = {
        "narrar": narrar,
        "persistir": persistir,
        "permitir_publicar": publicar,
        "canal": canal,
        "aprovado_por": aprovado_por,
        "skip_sync": skip_sync,
        "planejador": planejador,
    }
    estado["contexto"] = {}
    estado["tokens_consumidos"] = TOKENS_ZERO.copy()

    ferramentas = set(_tool_names(contracts))
    inicio = time.time()
    motivo_parada = "objetivo_alcancado"

    print(f"\n=== Agente {agent_path.name} ({estado['tipo_agente']}) ===")
    print(f"Objetivo: {estado['objetivo']}")
    print(f"Planejador: {planejador} | Competicoes: {', '.join(comps)}\n")

    while True:
        if estado["etapa"] >= estado["max_etapas"]:
            motivo_parada = "max_etapas_excedido"
            break
        if (time.time() - inicio) >= estado["limite_tempo_segundos"]:
            motivo_parada = "limite_tempo_excedido"
            break

        estado["etapa"] += 1
        _hook(contracts, "antes_da_etapa", f"etapa {estado['etapa']}: perceber/planejar")

        percepcao = perceber(estado)
        plano, uso = decide(estado, contracts, planejador=planejador)
        _add_tokens(estado, uso)

        problemas = validate_plan(plano, ferramentas)
        if problemas:
            _hook(contracts, "em_erro", f"plano invalido: {'; '.join(problemas)}")
            # uma nova chance com fixed se LLM quebrou
            if planejador == "llm":
                from palpitaria.agents.planner import plan_fixed

                plano = plan_fixed(estado, contracts)
                problemas = validate_plan(plano, ferramentas)
            if problemas:
                motivo_parada = "plano_invalido"
                estado["criterio_final"] = "; ".join(problemas)
                break

        acao = plano.get("proxima_acao")
        print(
            f"  [-] plano: {acao} "
            f"{plano.get('nome_ferramenta') or ''} "
            f"| {plano.get('criterio_sucesso', '')}",
            flush=True,
        )

        if acao == "FINALIZAR":
            faltando = _obrigatorias_ok(estado)
            if faltando:
                _hook(
                    contracts,
                    "em_erro",
                    f"FINALIZAR bloqueado — faltam: {', '.join(faltando)}",
                )
                # forca fixed para completar obrigatorias
                from palpitaria.agents.planner import plan_fixed

                plano = plan_fixed(estado, contracts)
                if plano.get("proxima_acao") == "FINALIZAR":
                    motivo_parada = "obrigatorias_incompletas"
                    estado["criterio_final"] = f"faltam: {faltando}"
                    break
                acao = plano.get("proxima_acao")
            else:
                estado["criterio_final"] = plano.get("criterio_sucesso")
                estado["concluido"] = True
                motivo_parada = "objetivo_alcancado"
                break

        if acao == "PERGUNTAR_USUARIO":
            pergunta = plano.get("pergunta") or "?"
            resposta = _ask_user(pergunta)
            estado["historico"].append(
                {
                    "etapa": estado["etapa"],
                    "tipo": "pergunta",
                    "pergunta": pergunta,
                    "resposta": resposta,
                    "plano": plano,
                }
            )
            continue

        # CHAMAR_FERRAMENTA
        nome = str(plano.get("nome_ferramenta") or "")
        if nome == "sincronizar_competicoes" and skip_sync:
            _hook(contracts, "apos_acao", "sync pulado (flag)")
            estado["chamadas_por_ferramenta"][nome] = (
                estado["chamadas_por_ferramenta"].get(nome, 0) + 1
            )
            estado["historico"].append(
                {
                    "etapa": estado["etapa"],
                    "plano": plano,
                    "ferramenta": nome,
                    "sucesso": True,
                    "resultado": {"status": "skipped"},
                }
            )
            estado.setdefault("contexto", {})["sync"] = {"status": "skipped"}
            continue

        if nome == "publicar_indicacoes" and not publicar:
            _hook(contracts, "em_erro", "publicar bloqueado (sem --publicar)")
            estado["historico"].append(
                {
                    "etapa": estado["etapa"],
                    "plano": plano,
                    "ferramenta": nome,
                    "sucesso": False,
                    "resultado": {"enviado": False, "motivo": "publicar_nao_autorizado_cli"},
                }
            )
            if _touch_progress(estado, nome):
                motivo_parada = "sem_progresso"
                break
            continue

        lim = _check_limits(estado, nome)
        if lim:
            motivo_parada = lim
            estado["criterio_final"] = lim
            break

        if nome in estado["acoes_sensiveis"] and not _ask_human_tool(nome):
            _record_tool(
                estado,
                plano=plano,
                nome=nome,
                argumentos={},
                resultado={"enviado": False, "motivo": "confirmacao_humana_negada"},
                ok=False,
            )
            motivo_parada = "confirmacao_humana_negada"
            break

        args = _enrich_args(nome, plano.get("argumentos_ferramenta") or {}, estado)
        _hook(contracts, "antes_da_acao", nome)
        try:
            resultado = call_tool(nome, **args)
            ok = True
            _hook(contracts, "apos_acao", f"{nome} ok")
        except Exception as exc:  # noqa: BLE001
            ok = False
            resultado = {"erro": str(exc)}
            _hook(contracts, "em_erro", f"{nome}: {exc}")

        _store_result(estado, nome, resultado if ok else {})
        _record_tool(
            estado,
            plano=plano,
            nome=nome,
            argumentos={k: v for k, v in args.items() if k != "texto"},
            resultado=_compact_result(nome, resultado),
            ok=ok,
        )

        if _touch_progress(estado, nome):
            motivo_parada = "sem_progresso"
            break

    artifact = _build_artifact(estado, inicio)
    artifact["motivo_parada"] = motivo_parada
    estado["resultado"] = artifact

    draft_text = artifact.get("rascunho_alerta") or ""
    if draft_text:
        print("\n--- Rascunho ---\n")
        print(draft_text)
    else:
        print("\n  [!] ciclo encerrou sem rascunho_diario")

    tokens = artifact.get("tokens_planejador") or {}
    print(
        f"\n=== Fim ({artifact['tempo_segundos']}s) "
        f"parada={motivo_parada} "
        f"tokens_planejador={tokens.get('total', 0)} ===\n"
    )
    return artifact


def _compact_result(nome: str, resultado: dict[str, Any]) -> dict[str, Any]:
    """Evita inflar historico / percepcao com textos longos."""
    if nome == "analisar_jogos_hoje":
        return {
            "total": resultado.get("total"),
            "dia_label": resultado.get("dia_label"),
            "homologadas": len(resultado.get("homologadas") or []),
            "alternativas": len(resultado.get("alternativas") or []),
            "descartes": len(resultado.get("descartes") or []),
            "sem_fundamento": len(resultado.get("sem_fundamento") or []),
            "homologadas_resumo": [
                {"jogo": r.get("jogo"), "mercado": r.get("mercado")}
                for r in (resultado.get("homologadas") or [])[:8]
            ],
            "sem_fundamento_resumo": [
                {"jogo": r.get("jogo"), "motivo": r.get("motivo")}
                for r in (resultado.get("sem_fundamento") or [])[:8]
            ],
        }
    if nome == "rascunho_diario":
        return {
            "requer_aprovacao": resultado.get("requer_aprovacao"),
            "dia_label": resultado.get("dia_label"),
            "contagens": resultado.get("contagens"),
            "texto_preview": (resultado.get("texto") or "")[:280],
        }
    if nome == "sincronizar_competicoes":
        return {
            "status": resultado.get("status"),
            "fixtures_por_comp": resultado.get("fixtures_por_comp"),
            "hoje_por_comp": resultado.get("hoje_por_comp"),
            "dia_label": resultado.get("dia_label"),
            "erros": resultado.get("erros"),
        }
    return resultado
