# loop.md

> Define como o agente roda.
> Controla o ciclo inteiro.
> Sem isso, nao existe agente.

---

```yaml
objetivo: fechar_dia_operacional

ciclo:
  max_etapas: 12

# etapas fixas do runtime (perceber -> planejar -> agir -> avaliar):
# etapas:
#   - perceber
#   - planejar
#   - agir
#   - avaliar

condicoes_parada:
  - objetivo_alcancado
  - max_etapas_excedido
  - sem_progresso
  - limite_tempo_excedido
  - confirmacao_humana_negada
```
