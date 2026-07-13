"""rascunho_diario — artefato texto; nunca envia."""

from __future__ import annotations

from typing import Any


def rascunho_diario(
    dia_label: str,
    resumo: dict[str, Any] | None = None,
    homologadas: list[dict[str, Any]] | None = None,
    descartes: list[dict[str, Any]] | None = None,
    alternativas: list[dict[str, Any]] | None = None,
    sem_fundamento: list[dict[str, Any]] | None = None,
    historico_ia: dict[str, Any] | None = None,
) -> dict[str, Any]:
    homologadas = homologadas or []
    descartes = descartes or []
    alternativas = alternativas or []
    sem_fundamento = sem_fundamento or []
    resumo = resumo or {}
    historico_ia = historico_ia or {}

    lines = [
        f"Palpitaria FC — rascunho diário {dia_label}",
        "",
        f"Homologadas: {len(homologadas)}",
        f"Alternativas: {len(alternativas)}",
        f"Descartes: {len(descartes)}",
        f"Sem fundamento: {len(sem_fundamento)}",
    ]

    if historico_ia:
        lines.append(
            "Histórico IA: "
            f"resolvidos={historico_ia.get('resolvidos', 0)} "
            f"pendentes={historico_ia.get('pendentes', 0)} "
            f"HIT={historico_ia.get('hits', 0)} "
            f"MISS={historico_ia.get('misses', 0)}"
        )

    if homologadas:
        lines.append("")
        lines.append("— Homologadas —")
        for row in homologadas:
            edge = row.get("edge")
            edge_s = f" edge={edge}" if edge is not None else ""
            lines.append(f"• {row.get('jogo')}: {row.get('mercado')}{edge_s}")

    if alternativas:
        lines.append("")
        lines.append("— Alternativas —")
        for row in alternativas:
            lines.append(f"• {row.get('jogo')}: {row.get('mercado')} (alt)")

    blocked = sem_fundamento or [
        d for d in descartes if "fundamento" in str(d.get("motivo", "")).lower()
    ]
    if blocked:
        lines.append("")
        lines.append("— Sem fundamento (silêncio) —")
        for row in blocked:
            lines.append(f"• {row.get('jogo')}: {row.get('motivo') or 'sem fundamento'}")

    if not homologadas and not alternativas:
        lines.append("")
        lines.append("Nenhuma entrada publicável hoje — silêncio é resultado válido.")

    if resumo.get("sync_status"):
        lines.append("")
        lines.append(f"Sync: {resumo.get('sync_status')}")

    lines.append("")
    lines.append("⚠ Rascunho — requer aprovação humana antes de qualquer envio.")

    texto = "\n".join(lines)
    return {
        "texto": texto,
        "requer_aprovacao": True,
        "dia_label": dia_label,
        "contagens": {
            "homologadas": len(homologadas),
            "alternativas": len(alternativas),
            "descartes": len(descartes),
            "sem_fundamento": len(sem_fundamento),
        },
    }
