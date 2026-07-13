# agent.md

> Identidade do agente.
> O que ele e, o que entrega, como se comporta.
> Sem isso, o agente e generico demais.

---

## Campos

| Campo | Tipo | Descricao |
|-------|------|-----------|
| `nome` | string | Identificador unico do agente. |
| `descricao` | string | O que o agente faz em uma frase. |
| `tipo` | string | Modo de operacao: `task_based`, `interactive`, `goal_oriented` ou `autonomous`. |
| `objetivo` | string | O que o agente deve alcancar. |
| `contrato_saida` | objeto | Estrutura do artefato final que o agente entrega. |

> **Tipos de agente:**
> - `task_based` — recebe tarefa bem definida, executa em poucas etapas, entrega artefato final
> - `interactive` — faz perguntas para remover ambiguidade antes de agir
> - `goal_oriented` — recebe objetivo amplo e transforma em plano executavel
> - `autonomous` — responde a eventos/triggers com limites rigidos

> **Palpitaria:** o modo diario padrao e `task_based`. Quando agendado (ex. 06:05 America/Sao_Paulo), o mesmo contrato opera como `autonomous` — sem mudar tools nem politicas.

---

```yaml
nome: palpitaria-diario
descricao: rotina diaria BSA/BSB(+WC) — sync, analise com gate de fundamento, rascunho de alerta e resolucao de historico IA
tipo: task_based

objetivo: fechar_dia_operacional

contrato_saida:
  formato: json
  campos_obrigatorios:
    - dia_label
    - sync
    - analises
    - homologadas
    - descartes
    - historico_ia
    - rascunho_alerta
    - requer_aprovacao_humana
  exemplo:
    dia_label: "2026-07-13"
    sync:
      bsa_fixtures: 10
      bsb_fixtures: 2
      status: ok
    analises:
      total: 4
      com_pick: 1
      sem_fundamento: 2
      descartadas: 1
    homologadas:
      - jogo: "Flamengo x Palmeiras"
        mercado: "Over 1.5"
        edge: 0.08
    descartes:
      - jogo: "America-MG x Londrina"
        motivo: "perfil sem fundamento (odds_implied)"
    historico_ia:
      resolvidos: 3
      pendentes: 0
    rascunho_alerta: "1 homologada · 2 sem fundamento · historico OK"
    requer_aprovacao_humana: true
```
