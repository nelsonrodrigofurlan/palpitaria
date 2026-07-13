"""publicar_indicacoes — ação sensível; stub seguro (não envia sozinho)."""

from __future__ import annotations

from typing import Any


def publicar_indicacoes(
    canal: str,
    texto: str,
    aprovado_por: str,
    *,
    confirmado: bool = False,
) -> dict[str, Any]:
    if not confirmado:
        return {
            "enviado": False,
            "referencia": "",
            "motivo": "confirmacao_humana_ausente",
        }
    if not (aprovado_por or "").strip():
        return {
            "enviado": False,
            "referencia": "",
            "motivo": "aprovado_por_obrigatorio",
        }
    if not (texto or "").strip():
        return {
            "enviado": False,
            "referencia": "",
            "motivo": "texto_vazio",
        }

    # Canal externo ainda não wired — recusa com motivo claro.
    return {
        "enviado": False,
        "referencia": "",
        "motivo": f"canal '{canal}' ainda nao implementado — rascunho permanece local",
        "canal": canal,
        "aprovado_por": aprovado_por,
        "preview_chars": len(texto),
    }
