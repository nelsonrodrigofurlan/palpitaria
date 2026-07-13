"""Adapters das ferramentas do agent.md / skills.md."""

from __future__ import annotations

from typing import Any, Callable

from palpitaria.agents.tools import analyze, draft, publish, resolve_ia, sync

ToolFn = Callable[..., dict[str, Any]]

TOOL_REGISTRY: dict[str, ToolFn] = {
    "sincronizar_competicoes": sync.sincronizar_competicoes,
    "analisar_jogos_hoje": analyze.analisar_jogos_hoje,
    "resolver_historico_ia": resolve_ia.resolver_historico_ia,
    "rascunho_diario": draft.rascunho_diario,
    "publicar_indicacoes": publish.publicar_indicacoes,
}


def get_tool(name: str) -> ToolFn:
    if name not in TOOL_REGISTRY:
        raise KeyError(f"ferramenta desconhecida: {name}")
    return TOOL_REGISTRY[name]


def call_tool(name: str, **kwargs: Any) -> dict[str, Any]:
    return get_tool(name)(**kwargs)
