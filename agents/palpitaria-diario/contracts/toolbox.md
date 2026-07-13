# toolbox.md

> Define o que o agente pode fazer.
> Quais ferramentas existem. Quais parametros aceitam.
> Se nao esta aqui — o agente nao pode executar.

---

```yaml
ferramentas:
  - nome: sincronizar_competicoes
    entrada:
      competicoes: list
      forcar: bool

  - nome: analisar_jogos_hoje
    entrada:
      competicoes: list
      limite: int

  - nome: resolver_historico_ia
    entrada:
      competicao: string

  - nome: rascunho_diario
    entrada:
      dia_label: string
      resumo: object
      homologadas: list
      descartes: list

  - nome: publicar_indicacoes
    entrada:
      canal: string
      texto: string
      aprovado_por: string
```
