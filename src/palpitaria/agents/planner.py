"""Planejador do agente — perceber + decidir (LLM ou ordem fixa)."""

from __future__ import annotations

import json
import re
from typing import Any

from palpitaria.config import settings

TOKENS_ZERO = {"prompt": 0, "completion": 0, "total": 0}
ACOES_VALIDAS = frozenset({"CHAMAR_FERRAMENTA", "FINALIZAR", "PERGUNTAR_USUARIO"})

# Ordem típica do contrato planner.md (fallback / --planejador fixed)
ORDEM_FIXA = (
    "sincronizar_competicoes",
    "analisar_jogos_hoje",
    "resolver_historico_ia",
    "rascunho_diario",
)


def _tool_names(contracts: dict[str, Any]) -> list[str]:
    return [
        h["nome"]
        for h in (contracts.get("habilidades") or {}).get("habilidades", [])
        if isinstance(h, dict) and h.get("nome")
    ]


def _summarize_result(resultado: Any, limit: int = 900) -> str:
    try:
        text = json.dumps(resultado, ensure_ascii=False, default=str)
    except TypeError:
        text = str(resultado)
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def perceber(estado: dict[str, Any]) -> str:
    partes = [
        f"Entrada: {estado.get('entrada') or 'dia operacional'}",
        f"Modo: {estado.get('tipo_agente', 'task_based')}",
        f"Competicoes: {', '.join(estado.get('competicoes') or [])}",
        f"Objetivo: {estado.get('objetivo')}",
    ]
    if estado.get("opcoes", {}).get("skip_sync"):
        partes.append("Flag: skip_sync=true (nao chamar sincronizar_competicoes)")
    if estado.get("opcoes", {}).get("permitir_publicar"):
        partes.append("Flag: humano pediu publicar (publicar_indicacoes permitido apos rascunho)")
    else:
        partes.append("Flag: NAO publicar — nao chamar publicar_indicacoes")

    for i, registro in enumerate(estado.get("historico") or [], start=1):
        nome = registro.get("ferramenta") or registro.get("plano", {}).get("nome_ferramenta") or "?"
        if registro.get("tipo") == "pergunta":
            partes.append(f"Etapa {i} [PERGUNTA]: {registro.get('pergunta')}")
            partes.append(f"  resposta: {registro.get('resposta')}")
            continue
        ok = registro.get("sucesso")
        partes.append(
            f"Etapa {i} [{nome}] sucesso={ok}: {_summarize_result(registro.get('resultado'))}"
        )

    usadas = list((estado.get("chamadas_por_ferramenta") or {}).keys())
    if usadas:
        partes.append(f"Ferramentas ja utilizadas: {', '.join(usadas)}")

    obrigatorias = estado.get("ferramentas_obrigatorias") or []
    faltando = [
        t
        for t in obrigatorias
        if t not in (estado.get("chamadas_por_ferramenta") or {})
        and not (t == "sincronizar_competicoes" and estado.get("opcoes", {}).get("skip_sync"))
    ]
    if faltando:
        partes.append(f"OBRIGATORIAS pendentes: {', '.join(faltando)}")
    else:
        partes.append("OBRIGATORIAS: todas satisfeitas (pode FINALIZAR apos rascunho)")

    partes.append(
        f"Progresso: etapa {estado.get('etapa', 0)}/{estado.get('max_etapas')} | "
        f"chamadas {estado.get('chamadas_ferramenta', 0)}/{estado.get('max_chamadas_ferramenta')}"
    )
    if estado.get("etapas_sem_progresso", 0) > 0:
        partes.append(
            f"ATENCAO: {estado['etapas_sem_progresso']} etapas sem progresso"
        )
    return "\n".join(partes)


def build_system_prompt(contracts: dict[str, Any], *, tipo_agente: str) -> str:
    agente = contracts.get("agente") or {}
    ciclo = contracts.get("ciclo") or {}
    habilidades = (contracts.get("habilidades") or {}).get("habilidades") or []
    planejador = contracts.get("planejador") or {}
    politicas = (contracts.get("regras") or {}).get("politicas") or []
    regras_p = planejador.get("regras") or []

    bloco = []
    for hab in habilidades:
        nome = hab.get("nome", "")
        desc = hab.get("descricao", "")
        ent = hab.get("entrada") or {}
        sai = hab.get("saida") or {}
        ent_s = ", ".join(f"{k}: {v}" for k, v in ent.items()) or "nenhuma"
        sai_s = ", ".join(f"{k}: {v}" for k, v in sai.items()) or "nenhuma"
        bloco.append(f"- {nome}: {desc}\n  entrada: {{{ent_s}}}\n  saida: {{{sai_s}}}")
    ferramentas = "\n".join(bloco) or "- nenhuma"

    texto_regras = "\n".join(f"- {r}" for r in regras_p)
    texto_pol = "\n".join(f"- {p}" for p in politicas)

    tipo_extra = ""
    if tipo_agente == "autonomous":
        tipo_extra = (
            "\nMODO AUTONOMOUS: responda ao trigger; limites rigidos; "
            "nunca publicar sem confirmacao humana.\n"
        )
    elif tipo_agente == "interactive":
        tipo_extra = (
            "\nMODO INTERACTIVE: use PERGUNTAR_USUARIO se faltar dado critico.\n"
        )

    return f"""Voce e o planejador do agente Palpitaria FC.

Agente: {agente.get('nome', 'agente')} — {agente.get('descricao', '')}
Tipo: {tipo_agente}
Objetivo: {ciclo.get('objetivo') or agente.get('objetivo')}

Ferramentas:
{ferramentas}

Responda APENAS JSON valido:
{{
  "proxima_acao": "CHAMAR_FERRAMENTA" | "FINALIZAR" | "PERGUNTAR_USUARIO",
  "nome_ferramenta": "string ou null",
  "argumentos_ferramenta": {{}},
  "criterio_sucesso": "string",
  "pergunta": "string se PERGUNTAR_USUARIO"
}}

CRITICO: proxima_acao deve ser exatamente um dos 3 valores acima.
Nunca use o nome da ferramenta como proxima_acao.
{tipo_extra}
Regras do planejador:
{texto_regras}

Politicas do produto:
{texto_pol}

Nao FINALIZAR enquanto houver ferramenta obrigatoria pendente.
Silencio (zero homologadas) ainda exige rascunho_diario.
"""


def validate_plan(plano: dict[str, Any], ferramentas: set[str]) -> list[str]:
    problemas: list[str] = []
    acao = plano.get("proxima_acao")
    if acao not in ACOES_VALIDAS:
        problemas.append(f"proxima_acao invalida: {acao!r}")
    if acao == "CHAMAR_FERRAMENTA":
        nome = plano.get("nome_ferramenta")
        if not nome:
            problemas.append("nome_ferramenta obrigatorio")
        elif nome not in ferramentas:
            problemas.append(f"ferramenta desconhecida: {nome}")
        args = plano.get("argumentos_ferramenta")
        if args is not None and not isinstance(args, dict):
            problemas.append("argumentos_ferramenta deve ser objeto")
    if acao == "PERGUNTAR_USUARIO" and not (plano.get("pergunta") or "").strip():
        problemas.append("pergunta obrigatoria para PERGUNTAR_USUARIO")
    if not (plano.get("criterio_sucesso") or "").strip():
        problemas.append("criterio_sucesso obrigatorio")
    return problemas


def plan_fixed(estado: dict[str, Any], contracts: dict[str, Any]) -> dict[str, Any]:
    """Ordem tipica do contrato — sem tokens."""
    usadas = estado.get("chamadas_por_ferramenta") or {}
    skip_sync = bool((estado.get("opcoes") or {}).get("skip_sync"))
    permitir = bool((estado.get("opcoes") or {}).get("permitir_publicar"))

    for nome in ORDEM_FIXA:
        if nome == "sincronizar_competicoes" and skip_sync:
            continue
        if nome not in usadas:
            return {
                "proxima_acao": "CHAMAR_FERRAMENTA",
                "nome_ferramenta": nome,
                "argumentos_ferramenta": {},
                "criterio_sucesso": f"{nome} executado",
            }

    if permitir and "publicar_indicacoes" not in usadas and "rascunho_diario" in usadas:
        return {
            "proxima_acao": "CHAMAR_FERRAMENTA",
            "nome_ferramenta": "publicar_indicacoes",
            "argumentos_ferramenta": {},
            "criterio_sucesso": "tentativa de publicacao apos confirmacao",
        }

    ctx = estado.get("contexto") or {}
    analises = ctx.get("analises") or {}
    hist = ctx.get("historico_ia") or {}
    return {
        "proxima_acao": "FINALIZAR",
        "nome_ferramenta": None,
        "argumentos_ferramenta": None,
        "criterio_sucesso": (
            f"homologadas={len(analises.get('homologadas') or [])} "
            f"descartes={len(analises.get('descartes') or [])} "
            f"sem_fundamento={len(analises.get('sem_fundamento') or [])} "
            f"pendentes_ia={hist.get('pendentes', '?')}"
        ),
    }


def _parse_json_content(content: str) -> dict[str, Any]:
    content = (content or "").strip()
    if not content:
        raise json.JSONDecodeError("empty", content, 0)
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
        if fence:
            data = json.loads(fence.group(1))
        else:
            start, end = content.find("{"), content.rfind("}")
            if start >= 0 and end > start:
                data = json.loads(content[start : end + 1])
            else:
                raise
    if not isinstance(data, dict):
        raise TypeError("plano nao e objeto")
    return data


def plan_with_llm(
    percepcao: str,
    contracts: dict[str, Any],
    *,
    tipo_agente: str,
) -> tuple[dict[str, Any], dict[str, int]]:
    if not settings.has_llm:
        return plan_fixed_from_percepcao(percepcao, contracts), TOKENS_ZERO.copy()

    from palpitaria.services.llm_client import chat_completion_json

    system = build_system_prompt(contracts, tipo_agente=tipo_agente)
    try:
        content, tokens = chat_completion_json(
            system,
            percepcao,
            temperature=0.1,
            max_tokens=500,
            feature="agent_planner",
        )
        plano = _parse_json_content(content)
        return plano, tokens
    except Exception as exc:  # noqa: BLE001
        print(f"  [!] planejador LLM falhou ({exc}) — usando ordem fixa", flush=True)
        return plan_fixed_from_percepcao(percepcao, contracts), TOKENS_ZERO.copy()


def plan_fixed_from_percepcao(percepcao: str, contracts: dict[str, Any]) -> dict[str, Any]:
    """Mock/fallback: sintetiza estado minimo a partir da percepcao (testes / sem key)."""
    usadas: dict[str, int] = {}
    for line in percepcao.splitlines():
        if line.startswith("Ferramentas ja utilizadas:"):
            for nome in line.split(":", 1)[1].split(","):
                nome = nome.strip()
                if nome:
                    usadas[nome] = 1
    skip = "skip_sync=true" in percepcao
    permitir = "humano pediu publicar" in percepcao
    estado = {
        "chamadas_por_ferramenta": usadas,
        "opcoes": {"skip_sync": skip, "permitir_publicar": permitir},
        "contexto": {},
    }
    return plan_fixed(estado, contracts)


def decide(
    estado: dict[str, Any],
    contracts: dict[str, Any],
    *,
    planejador: str = "llm",
) -> tuple[dict[str, Any], dict[str, int]]:
    percepcao = perceber(estado)
    tipo = str(estado.get("tipo_agente") or "task_based")
    if planejador == "fixed":
        return plan_fixed(estado, contracts), TOKENS_ZERO.copy()
    plano, tokens = plan_with_llm(percepcao, contracts, tipo_agente=tipo)
    return plano, tokens
