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
| Mercados MVP | Over 0,5 + Over 1,5 (filiais) |
| Filosofia | **Anti-zero-gols** — exclusão por dados, nunca presumir |
| Filiais | Cada tipo de entrada = unidade com P&L próprio |
| Stack | Python para dados/ML; frontend a definir |

## Princípios de produto

1. **Explicabilidade primeiro** — toda indicação mostra *como* chegou lá (variáveis, pesos, tendências).
2. **Três cenários sempre** — pessimista, realista, otimista; nunca uma única linha sem contexto.
3. **Dados antes de modelo** — pipeline de ingestão e qualidade antes de ML fancy.
4. **Validação privada** — track record antes de qualquer exposição pública.
5. **APIs free first** — cache agressivo; respeitar rate limits (ex.: 100 req/dia API-Football).
6. **Anti-zero-gols** — filtro de exclusão antes de qualquer indicação; sem dados = sem pick.

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
