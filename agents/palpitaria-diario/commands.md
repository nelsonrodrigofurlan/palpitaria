# commands.md

> Define a operacao do agente como produto.
> Cada comando e uma acao que o operador pode executar.

---

```yaml
comandos:
  - nome: rodar
    descricao: executa o ciclo diario (sync, analise, historico, rascunho)
    argumentos:
      - nome: --agente
        obrigatorio: true
        descricao: caminho para agents/palpitaria-diario
      - nome: --entrada
        obrigatorio: false
        descricao: dia operacional ou label da data (padrao = hoje SP)
      - nome: --modo
        obrigatorio: false
        descricao: task_based (padrao) ou autonomous (quando agendado)
      - nome: --comps
        obrigatorio: false
        descricao: lista BSA,BSB,WC
    exemplo: python -m palpitaria.agents rodar --agente agents/palpitaria-diario --comps BSA,BSB

  - nome: validar
    descricao: valida contratos do agente (agent/rules/skills/contracts alinhados)
    argumentos:
      - nome: --agente
        obrigatorio: true
        descricao: caminho da pasta do agente
    exemplo: python -m palpitaria.agents validar --agente agents/palpitaria-diario

  - nome: rascunho
    descricao: so gera rascunho a partir do estado atual do banco (sem sync)
    argumentos:
      - nome: --agente
        obrigatorio: true
      - nome: --comps
        obrigatorio: false
    exemplo: python -m palpitaria.agents rascunho --agente agents/palpitaria-diario
```
