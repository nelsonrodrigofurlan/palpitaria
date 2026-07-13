# skills.md

> Define as ferramentas.
> Nao implementa.
> So define interface.

---

## Campos

| Campo | Tipo | Descricao |
|-------|------|-----------|
| `habilidades` | lista | Interfaces das ferramentas. Runtime / adapters ligam em scripts e services reais. |

> **Bindings (runtime `palpitaria.agents.tools`):**
> - `sincronizar_competicoes` → `tools/sync.py` (ingest BSA/BSB/WC + Odds fallback BSB)
> - `analisar_jogos_hoje` → `tools/analyze.py` (`analyze_upcoming` + foundation + `narrate`)
> - `resolver_historico_ia` → `tools/resolve_ia.py`
> - `rascunho_diario` → `tools/draft.py` (texto local, sem envio)
> - `publicar_indicacoes` → `tools/publish.py` (**sensivel**; canal ainda stub)

---

```yaml
habilidades:
  - nome: sincronizar_competicoes
    descricao: sincroniza fixtures e dados BSA/BSB (e WC se pedido) via APIs; nao cria perfil odds_implied para homologacao
    entrada:
      competicoes: list
      forcar: bool
    saida:
      status: string
      fixtures_por_comp: object
      erros: list

  - nome: analisar_jogos_hoje
    descricao: analisa jogos da janela do dia com Poisson + gate de fundamento; LLM so narra se houver pick solido
    entrada:
      competicoes: list
      limite: int
    saida:
      total: int
      homologadas: list
      alternativas: list
      descartes: list
      sem_fundamento: list

  - nome: resolver_historico_ia
    descricao: liquida recomendacoes PENDING do IA historico com placares finais
    entrada:
      competicao: string
    saida:
      resolvidos: int
      pendentes: int
      hits: int
      misses: int

  - nome: rascunho_diario
    descricao: monta rascunho do alerta diario (humano aprova antes de qualquer envio)
    entrada:
      dia_label: string
      resumo: object
      homologadas: list
      descartes: list
    saida:
      texto: string
      requer_aprovacao: bool

  - nome: publicar_indicacoes
    descricao: envia alerta homologado a canal externo; SO apos confirmacao humana
    entrada:
      canal: string
      texto: string
      aprovado_por: string
    saida:
      enviado: bool
      referencia: string
```
