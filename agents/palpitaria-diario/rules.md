# rules.md

> Protege o sistema.
> Evita loop infinito.
> Define comportamento seguro.

---

## Campos

| Campo | Tipo | Descricao |
|-------|------|-----------|
| `ferramentas_obrigatorias` | lista | Ferramentas que devem ser chamadas antes de permitir FINALIZAR. |
| `limites` | objeto | Travas de etapas, tempo e chamadas. |
| `acoes_sensiveis` | lista | Ferramentas que exigem confirmacao humana. |
| `politicas` | lista | Regras injetadas no prompt da LLM. |

---

```yaml
ferramentas_obrigatorias:
  - sincronizar_competicoes
  - analisar_jogos_hoje
  - rascunho_diario

limites:
  max_etapas: 12
  sem_progresso: 3
  limite_tempo_segundos: 600
  chamadas_ferramenta:
    sincronizar_competicoes: 2
    analisar_jogos_hoje: 2
    resolver_historico_ia: 2
    rascunho_diario: 1
    publicar_indicacoes: 1
    total: 10

acoes_sensiveis:
  - publicar_indicacoes

politicas:
  - fundamento ou silencio: sem historico real (API/web), nao homologar pick — foundation.py
  - perfil provisoria ou odds_implied = descarte total; nao inventar entrada
  - modelo decide, LLM narra: nunca sobrescrever pick do prediction.py
  - descarte total = zero token de narrativa
  - rascunho_diario e obrigatorio antes de FINALIZAR
  - publicar_indicacoes exige confirmacao humana; nunca auto-publicar para grupo ou rede
  - competicoes padrao do dia: BSA e BSB; WC so se houver jogo na janela
  - CDB (Copa do Brasil) suportado via --comps (fallback CBF se football-data der 403) mas NAO entra no padrao ainda — decisao de produto pendente, ver context.md
  - janela operacional: 06:00 → 06:00 America/Sao_Paulo
  - P&L e comissao de filial nao sao responsabilidade deste agente
  - parar se nao houver progresso apos 3 etapas consecutivas
```
