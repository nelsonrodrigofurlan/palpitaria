# Estratégias de dados por competição

Documento de referência para o skill **betfair** (Palpitaria FC).  
Cada competição tem janela, volume de jogos e fontes diferentes — não reutilizar a mesma receita cegamente.

## Princípio geral

| Fase | Regra |
|------|--------|
| Sem histórico | **Não chutar** — buscar dados (web/API) ou descartar |
| Histórico parcial | Complementar com a fonte secundária da competição |
| Histórico maduro | API como fonte primária; web para contexto/bastidores |

---

## Copa do Mundo (MVP atual — WC 2026)

### Por que é especial

- Campeonato **curto**: campeão joga **7 partidas** no máximo.
- Muitas seleções chegam com **0 jogos finalizados** na API no dia da estreia.
- Esperar só `football-data.org` FINISHED deixa buraco nos primeiros jogos de cada grupo.

### Estratégia Palpitaria FC

```
Passo 3 /analyze (sempre):
  1. Perfil híbrido API+web para TODAS as seleções de hoje (persiste no banco)
  2. Filtro anti-zero-gols usa perfil híbrido (amistosos + eliminatórias + Copa)
  3. Bastidores web do dia (mesmo se descartado — informa a decisão)
  4. refine_best_pick (LLM) — decisão final integra stats + web + bastidores + contexto
  5. explain_analysis — texto alinhado ao pick refinado
```

**Princípio:** web não é “bootstrap” temporário — é camada permanente na Copa. A API enriquece jogos finalizados da Copa; a web mantém histórico amplo e bastidores.

### Fontes

| Camada | Uso |
|--------|-----|
| football-data.org | Calendário, elencos, árbitro, jogos FINISHED da Copa |
| DuckDuckGo + LLM | Placares recentes (web_research / **hybrid** — sempre) |
| LLM | Bastidores, clima, **decisão de mercado**, explicação |

### Config (`config.py`)

- `wc_web_profile_min_matches` — mínimo de jogos com placar explícito na web (default: 3)
- `wc_web_profile_refresh_hours` — 0 = atualiza perfil híbrido a cada passo 3

### Código

- `services/wc_profile_web.py` — perfil **híbrido persistente** (API + web)
- `services/explainer.py` — `refine_best_pick()` — web impacta diretamente o mercado
- `services/scraper.py` — bastidores mesmo em jogos descartados
- `main.py` `/analyze` — pipeline completo

### Anti-alucinação (web stats)

- LLM extrai **só placares explícitos** nos snippets
- Stats numéricos calculados em Python (mesmas métricas da API)
- Amostra mínima configurável; abaixo disso = sem perfil

---

## Campeonato Brasileiro (Série A) — planejado

### Expectativa

- Temporada longa (~38 rodadas), times com histórico denso na API.
- **API primária** desde o início (football-data.org ou API-Football).
- Web complementar: desfalques, clima, escalação provável, notícias locais.

### Diferença vs Copa

| | Copa | Brasileirão |
|---|------|-------------|
| Jogos/time/temporada | ~3–7 | ~38 |
| Buraco API no início | Alto (estreias) | Baixo |
| Web stats históricos | **Essencial** no passo 3 | Opcional / bastidores |
| Janela de análise | Só jogos de hoje | Rodada atual + forma recente |

### Skill futuro

Criar `competition-brasileirao.md` quando iniciar liga piloto pós-Copa.

---

## Copa do Brasil — planejado

- Mata-mata + times de divisões mistas → perfis desiguais.
- API para jogos; web forte para **motivação** (rotação, prioridade Série A vs Copa).
- Cuidado com amostra: times da Série C podem ter pouco histórico recente na API top.

---

## Checklist ao trocar de competição

1. Definir fonte primária e mínimo de jogos amostrados.
2. Decidir se web stats entram no passo de sync ou só no analyze.
3. Ajustar thresholds em `config.py` (não hardcodar WC no analyzer).
4. Atualizar este arquivo + mensagens da UI.
5. Testes com fixture real da competição.

---

## Histórico de decisões

| Data | Decisão |
|------|---------|
| 2026-06-14 | Copa: perfil híbrido API+web persistente; refine_best_pick integra web na decisão de mercado |
