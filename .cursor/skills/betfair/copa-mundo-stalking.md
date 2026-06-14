# Copa do Mundo — Stalking de seleções

Skill de referência para **Palpitaria FC**: onde buscar informação sobre **todas** as seleções da Copa (convocações, lesões, amistosos, stats, rankings, bastidores).

> Complementa [competition-data-strategies.md](competition-data-strategies.md) (pipeline técnico) com o **mapa de fontes** para humanos e agentes.

---

## Objetivo

Acompanhar praticamente tudo sobre as seleções que disputam a Copa:

- Convocações e cortes
- Lesões e suspensões
- Amistosos e eliminatórias (histórico de gols)
- Estatísticas e rankings
- Análises táticas e notícias de bastidores

---

## Conjunto recomendado (≈90% da cobertura)

Para eficiência, priorizar estes **6**:

| # | Fonte | Papel |
|---|--------|--------|
| 1 | [FIFA.com](https://www.fifa.com) | Oficial: convocações, calendário, ranking FIFA, entrevistas |
| 2 | [ESPN FC](https://www.espn.com/soccer) | Notícias diárias e análise tática internacional |
| 3 | [Transfermarkt](https://www.transfermarkt.com) | Elenco, lesões, valores, convocações |
| 4 | [FBref](https://fbref.com) | Stats avançadas de seleções e jogadores |
| 5 | [FotMob](https://www.fotmob.com) | Partidas ao vivo, alertas, escalações |
| 6 | [Elo Ratings](https://eloratings.net) | Força real das seleções (alternativa ao ranking FIFA) |

---

## Notícias e cobertura diária

| Fonte | URL | Uso |
|-------|-----|-----|
| **FIFA** | https://www.fifa.com | Oficial: convocações, calendário, rankings, entrevistas |
| **ESPN FC** | https://www.espn.com/soccer | Notícias internacionais, análises táticas, grandes seleções |
| **The Athletic** | https://theathletic.com | Reportagens profundas, futebol internacional |
| **BBC Sport Football** | https://www.bbc.com/sport/football | Seleções europeias, torneios grandes |
| **Sky Sports Football** | https://www.skysports.com/football | Notícias rápidas, jogadores, seleções |

**Palpitaria FC hoje:** bastidores via DuckDuckGo + LLM — snippets podem vir desses sites, mas **sem busca direcionada por domínio** ainda.

---

## Estatísticas e dados

| Fonte | URL | Uso |
|-------|-----|-----|
| **Transfermarkt** | https://www.transfermarkt.com | Mercado, convocações, lesões, elenco, histórico |
| **Soccerway** | https://www.soccerway.com | Jogos, escalações, resultados, stats detalhadas |
| **WorldFootball.net** | https://www.worldfootball.net | Histórico completo de seleções e competições |
| **FBref** | https://fbref.com | Stats avançadas (analistas, xG quando disponível) |
| **FotMob** | https://www.fotmob.com | Ao vivo, alertas, lineups |

**Palpitaria FC hoje:**

| Dado | Fonte atual | Fonte alvo (stalking) |
|------|-------------|------------------------|
| Placares / histórico híbrido | DDG + LLM (`wc_profile_web.py`) | Soccerway, WorldFootball, FBref |
| Elenco convocado | football-data.org API | Transfermarkt, FIFA |
| Stats numéricas filtro | Perfil híbrido (API Copa + web) | FBref, Soccerway |

---

## Rankings e desempenho internacional

| Fonte | URL | Uso |
|-------|-----|-----|
| **Ranking FIFA** | https://www.fifa.com/fifa-world-ranking | Referência oficial, evolução das seleções |
| **Elo Ratings** | https://eloratings.net | Muitos analistas preferem à FIFA para “força real” |

**Palpitaria FC hoje:** ranking **não** entra no modelo — candidato futuro como feature de contexto.

---

## Análise tática e scouting

| Fonte | URL | Uso |
|-------|-----|-----|
| **Total Football Analysis** | https://totalfootballanalysis.com | Artigos táticos sobre seleções |
| **StatsBomb** | https://statsbomb.com/articles/soccer | Conteúdo analítico, dados avançados |

**Palpitaria FC hoje:** LLM resume bastidores genéricos; artigos táticos entram só se aparecerem nos snippets DDG.

---

## O que o app já faz vs. stalking completo

### Implementado (passo 3 — Gerar Leituras)

```
Seleções de HOJE
  → perfil híbrido (API Copa + placares web via DDG/LLM)
  → bastidores (DDG: queries PT + EN por seleção)
  → contexto jogo (clima, árbitro, gramado)
  → refine_best_pick + explicação
```

- **Escopo:** só times que jogam **no dia** (não as 48 de uma vez).
- **Web:** busca genérica (DuckDuckGo), não `site:fifa.com` etc.
- **Persistência:** `TeamProfile` (stats + insights), `FixtureReport` (leitura do jogo).

### Não implementado (backlog stalking)

- [ ] Queries direcionadas por domínio (`site:transfermarkt.com`, `site:fbref.com`, …)
- [ ] Scrapers dedicados por fonte (Transfermarkt lesões, FBref team stats)
- [ ] Ranking FIFA / Elo como variável no filtro ou LLM
- [ ] Monitoramento contínuo das 48 seleções fora do dia de jogo
- [ ] FotMob / alertas push

---

## Queries DDG sugeridas (próxima evolução)

Ao estender `scraper.py` / `wc_profile_web.py`, usar padrões:

```
{seleção EN} site:transfermarkt.com injuries squad World Cup 2026
{seleção PT} site:fifa.com convocação Copa 2026
{seleção EN} site:fbref.com national team stats 2025 2026
{mandante} vs {visitante} site:espn.com OR site:bbc.com World Cup 2026
{seleção EN} site:eloratings.net
{seleção EN} site:soccerway.com results friendly
```

Manter **anti-alucinação**: só extrair o que estiver explícito no snippet; stats numéricos preferir FBref/Soccerway com parser dedicado quando existir.

---

## Quando usar cada camada (agente / dev)

| Tarefa | Fonte primária | Fallback |
|--------|----------------|----------|
| Calendário e placar oficial Copa | football-data.org | FIFA.com |
| Estreia sem jogo API | wc_profile_web (DDG) | Soccerway, WorldFootball |
| Lesão / convocação | Transfermarkt + FIFA | ESPN, DDG + LLM |
| Bastidores / clima / árbitro | scraper atual (DDG) | ESPN, BBC, Sky |
| Força relativa seleções | Elo (contexto LLM) | Ranking FIFA |
| Stats avançadas jogador/time | FBref | Transfermarkt |

---

## Relação com outros skills

- Pipeline e thresholds: [competition-data-strategies.md](competition-data-strategies.md)
- Contexto geral do produto: [context.md](context.md)
- Brasileirão / Copa do Brasil: skills **futuros** (fontes locais: GE, UOL Esporte, etc.)

---

## Histórico

| Data | Nota |
|------|------|
| 2026-06-14 | Documento criado a partir da curadoria do fundador (fontes stalking WC) |
| 2026-06-14 | Queries `site:` core-6 implementadas em `wc_stalking_queries.py` + `search_web_stalking` |
