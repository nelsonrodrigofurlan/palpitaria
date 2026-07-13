# planner.md

> Define como a LLM decide.
> Isso nao e prompt. E contrato.
> Obriga a LLM a responder estruturado.

---

```yaml
formato_saida:
  proxima_acao: CHAMAR_FERRAMENTA | FINALIZAR | PERGUNTAR_USUARIO
  nome_ferramenta: opcional
  argumentos_ferramenta: opcional
  criterio_sucesso: obrigatorio
  pergunta: opcional (obrigatorio se PERGUNTAR_USUARIO)

regras:
  - sempre definir proxima_acao
  - nunca retornar texto livre
  - ordem tipica: sincronizar_competicoes → analisar_jogos_hoje → resolver_historico_ia → rascunho_diario → FINALIZAR
  - so FINALIZAR apos rascunho_diario
  - publicar_indicacoes so se humano pediu explicitamente e acao sensivel foi confirmada
  - se analisar_jogos_hoje retornar so descartes/sem_fundamento, ainda assim gerar rascunho (silencio e um resultado valido)
  - criterio_sucesso do FINALIZAR deve citar counts de homologadas, descartes e pendentes IA
  - usar PERGUNTAR_USUARIO so se faltar canal/aprovacao para publicar ou competicao ambigua
```
