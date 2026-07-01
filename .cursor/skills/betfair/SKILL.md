---
name: betfair
description: >-
  Guia o projeto de analytics preditivo para apostas esportivas (greenfield).
  Use em toda conversa neste repo: coleta de dados, modelagem, explicabilidade,
  três cenários por indicação, APIs de futebol, e construção incremental do app.
  Acione para apostas esportivas, odds, modelos preditivos, scraping ou
  continuidade do projeto.
---

# Projeto — Palpitaria FC

Skill de contexto e workflow. Leia [context.md](context.md) antes de propor código ou arquitetura.

## Regra crítica de marca

**Nunca** usar o nome Betfair em UI, strings de código visíveis, README público, nomes de pacotes ou documentação externa. O nome oficial é **Palpitaria FC**.

## Estado atual

| Item | Valor |
|------|-------|
| Fase | **1 → 2** — MVP Copa 2026; fundação técnica |
| Repositório | Greenfield — sem commits |
| Produto | Modelo preditivo explicável para apostas esportivas |
| Saída core | 3 cenários (pessimista / realista / otimista) + explicabilidade |
| MVP imediato | **Copa do Mundo 2026** (API-Football `league=1`, `season=2026`) |
| Liga pós-Copa | Brasileirão Série A |
| Dados | 12 meses histórico; seleções, jogadores, eliminatórias |
| Coleta | APIs públicas → pagas → scraping complementar |
| Exchange | API oficial descartada; hipótese sessão browser (P&D) |
| Público MVP | Apenas o fundador (validação privada) |
| Esporte | Futebol |
| Mercados MVP | Over 0,5 + Over 1,5 + Over 2,5 (Prioridade Total) |
| Filosofia | **Foco em Gols** — Priorizar mercados Over; **Liberdade de Descarte Total** se houver dúvida ou dados insuficientes |
| Filiais | Cada tipo de entrada = unidade com P&L próprio; comissão % por filial (padrão 6,5%) |
| Saída Homologada | Apenas mercados de Gols com base sólida fundamentada |
| Saída Alternativa | Vencedor (1X2) e Lay Correct Score (apenas se houver critério mínimo; senão descarta) |
| Especialização | **Skills por Campeonato** — Ver pasta `.cursor/skills/competitions/` |
| Stack | Python para dados/ML; frontend FastAPI + HTMX |

## Filiais — lançamento manual e import CSV

O P&L no app é **sempre líquido**, com a **comissão da filial** descontada nos greens — igual ao lançamento manual em `/branches`.

| Tipo | GREEN (líquido) | RED |
|------|-----------------|-----|
| BACK | `stake × (odd − 1) × (1 − comissão%)` | `−stake` |
| LAY | `stake × (1 − comissão%)` | `−stake × (odd − 1)` |

### Import do CSV de apostas liquidadas (exchange)

Script: `scripts/import_betfair_csv.py`. Colunas usadas: `Realizada`, `Descrição`, `Tipo`, `Cotações`, `Valor Apostado (R$)`, `Status`.

**Regra obrigatória:** a coluna **Lucro/Perda** do export é **bruta** nos greens. **Nunca** gravar esse valor direto no `profit_loss`. Usar `betfair_csv_net_pl()` / `compute_bet_pl()` com:

- `stake` e `odd` do CSV
- `outcome` WIN/LOSS a partir de `Status`
- `commission_rate` da **filial** de destino
- `side` da filial (BACK ou LAY — hedges/trader vão para filiais LAY/BACK corretas)

Marcar import com `[BF:{id}]` na descrição para idempotência. Após import ou mudança de comissão: `python scripts/import_betfair_csv.py --recalc`.

Mapeamento filial (resumo): Over 0,5 / 1,5 / 2,5 BACK → filiais over; 1X2 → match odds; AH +1 → Handicap; Under 4,5 → under; Correct Score LAY → lay CS; demais LAY/BACK trader → filiais Trader.

## Arquitetura em camadas (análise do dia)

| Camada | O quê | Tokens / custo |
|--------|--------|----------------|
| **0** | Sync API (fixtures, odds) | Quase zero LLM |
| **1** | Filtro numérico anti-zero (`analyze_fixture`) | Zero LLM |
| **2** | LLM curto: `refine_best_pick` + `explain_analysis` + **`build_strategy_card`** | 1 chamada/jogo candidato+descartado |
| **3** | Web stalking **condicional** — perfil híbrido se stale (`wc_web_profile_refresh_hours`, default 48h); bastidores/contexto só se cache ausente | Só quando necessário |
| **4** | Chat `/chat` — contexto do **banco** (report, strategy_card, perfis, odds); web ao vivo só se usuário pedir notícias | Sem re-scraping padrão |

**FixtureStrategyCard:** `services/strategy_card.py` → JSON 2–3 estratégias (mercado, tese, risco, odd hint) → `fixture_reports.strategy_json` → partial `partials/strategy_card.html` na home.

## Especialização por Campeonato

O Palpitaria FC opera com "Módulos de Especialista" para cada competição, pois cada uma possui dinâmicas únicas:

- **Copa do Mundo (WC)**: Foco em amostras curtas, perfil híbrido (API+Web) e volatilidade. Ver `.cursor/skills/competitions/WC.md`.
- **Brasileirão (BSA)**: Foco em pontos corridos, mando de campo e amostras longas. Ver `.cursor/skills/competitions/BSA.md`.
- **Copa do Brasil (CDB)**: Foco em mata-mata, motivação e rotação de elenco. Ver `.cursor/skills/competitions/CDB.md`.

Ao analisar um jogo, identifique o `competition_code` e aplique as regras do especialista correspondente.

## Princípios de produto

1. **Prioridade Máxima: GOLS** — O produto busca Gols (Over 0.5, 1.5, 2.5). Esta é a base do Palpitaria FC.
2. **Base Fundamentada ou Descarte** — Nunca deduzir sem base sólida. Se houver dúvida ou dados insuficientes, **descarte** o palpite.
3. **Homologada vs Alternativa** — Apenas mercados de Gols entram como "Homologadas". Mercados de Vencedor (1X2) e Lay Correct Score são estritamente "Alternativos", a menos que haja um favorito absoluto com base de dados massiva.
4. **Liberdade de Descarte** — O sistema não é obrigado a palpitar 100% dos jogos. Se um jogo for diagnosticado como "muito abaixo" estatisticamente, ele deve ser descartado totalmente, não aparecendo nem como alternativa.
5. **Explicabilidade primeiro** — toda indicação mostra *como* chegou lá (variáveis, pesos, tendências).
5. **Três cenários sempre** — pessimista, realista, otimista; nunca uma única linha sem contexto.
6. **Dados antes de modelo** — pipeline de ingestão e qualidade antes de ML fancy.
7. **Validação privada** — track record antes de qualquer exposição pública.
8. **Anti-zero-gols** — filtro de exclusão antes de qualquer indicação; sem dados = sem pick.

## Dia operacional (análises)

- **Janela:** `06:00` → `06:00` do dia seguinte em `America/Sao_Paulo` (`app_day_start_hour=6`).
- **Leituras / home / pipeline:** só fixtures com kickoff nessa janela (`for_today_only=True`).
- **Perfis:** passo 2 atualiza seleções do dia **e** faz backfill de quem ficou sem perfil — isso **não** expande análises para jogos passados.

## Painel root (só fundador)

| Menu | Rota | Função |
|------|------|--------|
| Skills do Agente | `/admin/skills` | Lê `.cursor/skills/` em linguagem natural + markdown completo |
| Fontes Scouting | `/admin/fontes` | URLs extras (global ou por seleção) → queries no stalking |

**Inteligência Coletiva** (`/chat`): bate-papo adulto — a IA lê, entende e **opina** (a favor ou contra) com dados; **não fica só concordando**. Palpite pré-live oficial **não muda** pelo chat (pipeline já rodou). Papinho ou palpite firme forçado → escapada à francesa: orientar **acompanhar como TRADER** no live, não pré-live. Incorporar só **fatos** (`insight_type: fact`). Skill global: `palpitaria-inteligencia-coletiva`.

**Ao criar ou alterar skills:** editar arquivos em `.cursor/skills/`; a página admin reflete pelo `mtime` do arquivo. Novo arquivo `.md` → adicionar resumo em `PLAIN_PURPOSE` em `services/skills_reader.py`.

## Pipeline de decisão (gols)

```
Fixtures Copa → Perfil seleções (API + web na estreia) → Filtro EXCLUSÃO (0-gol?) 
    → DESCARTA se sim
    → Score potencial gols → Ranqueia candidatos
    → Verifica desfalques (injuries/lineup)
    → 3 cenários + explicabilidade → Filiais over_0_5 / over_1_5
```

**Copa do Mundo:** perfil híbrido API+web **sempre**; LLM refina mercado com bastidores + histórico web. Ver [competition-data-strategies.md](competition-data-strategies.md).

## Princípios de engenharia

1. **Contexto antes de código** — registrar decisões em `context.md`.
2. **Escopo mínimo** — vertical slice (uma liga, um mercado, um jogo) antes de generalizar.
3. **Idioma** — português com o usuário; código/commits em inglês.
4. **Secrets** — nunca commitar credenciais, cookies de sessão ou `.env`.
5. **Scraping consciente** — respeitar ToS; preferir APIs; scrapers como complemento documentado.

## Workflow por fase

### Fase 0 — Descoberta ✅ parcial

```
[x] Problema: modelo preditivo explicável para apostas
[x] Público: validação solo (+ root user; freemium futuro)
[x] Exchange: uso pessoal; sem marca no produto
[x] Dados: 12 meses, máximo de variáveis
[x] Esporte: futebol
[x] Liga piloto: Brasileirão Série A
[x] Mercados: gols, favorito óbvio, lay correct score
[x] Filiais: conceito definido; detalhes a amadurecer
[ ] Filial piloto (over 0,5 vs 1,5)
[ ] Formato do app (web vs CLI)
```

### Fase 1 — Visão e escopo (atual)

- Mapear APIs públicas viáveis → ver tabela em `context.md`.
- Definir liga piloto e mercados MVP.
- Desenhar arquitetura de dados (ingestão → storage → features → modelo → UI).
- Propor stack; **aguardar aprovação**.

### Fase 2 — Fundação técnica

- Estrutura Python (pyproject/requirements, `.gitignore`).
- Pipeline ingestão batch com cache local.
- Schema de dados para partidas, eventos, cartões, gols por minuto.
- Primeiro perfil estatístico de time.

### Fase 3 — Modelo e UI

- Feature engineering (agressividade, timing de gols, comportamento pós-gol).
- Três cenários + explicabilidade.
- Dashboard mínimo para validação pessoal.

## Variáveis prioritárias

| Grupo | Métricas |
|-------|----------|
| Resultado | W/D/L, gols pró/contra, clean sheets |
| Comportamento | Agressivo vs retranqueiro, posse (se disponível) |
| Disciplina | Amarelos/vermelhos por jogo e período |
| Timing | Distribuição de gols por faixa de minuto (0-15, 16-30…) |
| Reação | Performance após marcar/sofrer gol |
| Contexto | Mandante/visitante, competição (liga vs copa) |
| Jogador | Gols, assistências, cartões, minutos |

## Fontes de dados (referência)

| Fonte | Uso |
|-------|-----|
| API-Football | Stats, eventos, cartões, lineups; 100 req/dia free |
| football-data.org | 12 ligas top; fixtures, tabelas; cartões só pago |
| FBref / Transfermarkt | Scraping complementar (fragilidade alta) |
| Exchange (P&D) | Odds via sessão — testar viabilidade |

## Decisões em aberto

Sincronizar com `context.md`:

| Decisão | Status |
|---------|--------|
| Esporte/liga piloto | ✅ Futebol / Brasileirão Série A |
| Filial piloto | Aberto (over 0,5 vs 1,5) |
| Formato app | Aberto (web dashboard provável) |
| Banco de dados | Aberto |
| Nome do produto | Aberto |

## Ao iniciar cada sessão

1. Ler `context.md`.
2. Resumir estado e próximo passo em 2–3 linhas.
3. Não pular ingestão de dados para codificar UI.

## Anti-padrões

- Modelo ML antes de ter dados limpos de 12 meses.
- UI elaborada antes de uma indicação explicável funcionar end-to-end.
- Depender de scraping frágil como fonte primária.
- Mencionar Betfair em qualquer superfície do produto.
- Publicar indicações externamente na fase de validação.
- Commits ou PRs não solicitados.

## Recursos

- Contexto vivo: [context.md](context.md)
- Estratégias por competição: [competition-data-strategies.md](competition-data-strategies.md)
- Stalking seleções Copa (fontes FIFA, ESPN, Transfermarkt, FBref…): [copa-mundo-stalking.md](copa-mundo-stalking.md)
- API-Football docs: https://www.api-football.com/documentation-v3
- football-data.org: https://www.football-data.org/documentation/api
