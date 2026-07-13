"""Testes do runtime mínimo de agentes (contratos + draft + publish stub)."""

from __future__ import annotations

from pathlib import Path

from palpitaria.agents import default_agent_path
from palpitaria.agents.contracts import create_state, load_contracts, load_yaml_from_md
from palpitaria.agents.tools.draft import rascunho_diario
from palpitaria.agents.tools.publish import publicar_indicacoes
from palpitaria.agents.validator import validate_agent


def test_palpitaria_diario_contracts_valid():
    path = default_agent_path()
    assert path.exists(), f"agente ausente: {path}"
    result = validate_agent(path)
    assert result.ok, result.errors


def test_load_contracts_has_tools():
    contracts = load_contracts(default_agent_path())
    nomes = {h["nome"] for h in contracts["habilidades"]["habilidades"]}
    assert "sincronizar_competicoes" in nomes
    assert "rascunho_diario" in nomes
    assert "publicar_indicacoes" in nomes
    state = create_state(contracts, competicoes=["BSA"])
    assert state["objetivo"] == "fechar_dia_operacional"
    assert "publicar_indicacoes" in state["acoes_sensiveis"]


def test_rascunho_diario_silencio():
    out = rascunho_diario(
        dia_label="2026-07-13",
        homologadas=[],
        sem_fundamento=[
            {"jogo": "A x B", "motivo": "perfil sem fundamento"},
        ],
        historico_ia={"resolvidos": 0, "pendentes": 0, "hits": 0, "misses": 0},
    )
    assert out["requer_aprovacao"] is True
    assert "silêncio" in out["texto"].lower() or "silencio" in out["texto"].lower()
    assert "A x B" in out["texto"]


def test_publicar_requer_confirmacao():
    denied = publicar_indicacoes(canal="whatsapp", texto="oi", aprovado_por="tinoco", confirmado=False)
    assert denied["enviado"] is False
    assert denied["motivo"] == "confirmacao_humana_ausente"

    stub = publicar_indicacoes(
        canal="whatsapp",
        texto="oi",
        aprovado_por="tinoco",
        confirmado=True,
    )
    assert stub["enviado"] is False
    assert "nao implementado" in stub["motivo"]


def test_yaml_block_parser(tmp_path: Path):
    md = tmp_path / "x.md"
    md.write_text("# t\n\n```yaml\nnome: demo\nlista:\n  - a\n```\n", encoding="utf-8")
    data = load_yaml_from_md(md)
    assert data["nome"] == "demo"
    assert data["lista"] == ["a"]
