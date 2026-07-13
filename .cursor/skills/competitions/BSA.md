# Especialista: Brasileirão Série A (BSA)

Este skill define o comportamento do Palpitaria FC para o Campeonato Brasileiro Série A.

## Características do Campeonato
- **Pontos Corridos**: 38 rodadas, longa duração.
- **Vantagem de Casa (Home Edge)**: fator determinante no Brasil.
- **Desgaste de Elenco**: viagens longas e calendário apertado.
- **Equilíbrio**: poucos favoritos absolutos fora de casa.

## Regras de Análise (BSA)
1. **Amostra robusta**: mínimo **5 jogos** recentes para análise STRONG.
2. **API como fonte primária**: football-data.org `BSA` — web só complementar (desfalques).
3. **Fator local**: mando entra no λ do Poisson (`home_advantage_goals`).
4. **Janela de forma**: últimos 5 jogos com peso maior no perfil.
5. **Modelo decide**: `prediction.py` gera P(Over) e pick; LLM só narra (`narrate.py`).

## Prioridades de Mercado (BSA)
- **Over 1.5**: mercado core (times ofensivos em casa).
- **Over 0.5**: conservador anti-zero.
- **Over 2.5**: só com P modelo alta e λ elevado.
- **BTTS / Lay 0-0**: secundários conforme probs.

## Código
- Perfil: `services/competitions.py` → `BSA`
- Odds API sport: `soccer_brazil_campeonato`
