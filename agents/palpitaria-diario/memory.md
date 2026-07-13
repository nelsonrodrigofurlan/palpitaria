# memory.md

> Define a memoria curta do agente.
> O que guardar. O que descartar.
> Como resumir a execucao no final.

---

```yaml
memoria_curta:
  guardar:
    - resultado_de_ferramenta
    - decisao_do_planejador
    - picks_homologados
    - motivos_de_descarte
    - erros_de_sync
  descartar:
    - prompt_sistema_completo
    - payloads_llm_completos
    - odds_brutas_repetidas
    - secrets_e_tokens
  max_registros: 24

resumo_final:
  max_linhas: 8
  campos:
    - objetivo
    - etapas_executadas
    - ferramentas_chamadas
    - homologadas_count
    - descartes_count
    - historico_ia
    - resultado_final
    - proximos_passos
```
