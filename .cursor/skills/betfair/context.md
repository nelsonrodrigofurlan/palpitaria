# Palpitaria FC — Contexto do Projeto

Documento vivo — atualizar a cada descoberta. Última revisão: 2026-06-13.

> **Regra de marca:** Usar o nome **Palpitaria FC**. O domínio oficial é `palpitariafc.com.br`. Nunca usar o nome Betfair em UI ou documentação pública.

## Visão

Plataforma de **análise preditiva para apostas esportivas** com a alma do futebol brasileiro. Combina estatísticas pesadas com uma leitura de jogo leve e explicável, focando sempre em fugir do zero e buscar o show de gols.

## Intenção do fundador

Construir um modelo preditivo fundamentado em dados — não palpites. O usuário precisa entender **como** cada indicação foi construída (quais variáveis pesaram, quais tendências sustentam a leitura). Validar pessoalmente antes de expor a qualquer público; exposição sem resultado consistente destrói credibilidade.

## Público-alvo

| Aspecto | Resposta |
|---------|----------|
| Quem usa | Apenas o fundador (fase de validação) |
| Contexto de uso | Análise pré-jogo para decisão de entrada em exchange |
| Expansão futura | Possível, somente após track record comprovado |
| Nível técnico | Fundador técnico; colaboração com agente para implementação |

## Plataforma de apostas (uso pessoal)

| Pergunta | Resposta |
|----------|----------|
| Plataforma usada | Exchange Betfair (uso pessoal) |
| API oficial | Descartada — custo/benefício não compensa |
| Alternativa em estudo | Acesso via sessão autenticada no browser — **hipótese a testar** |
| Nome da plataforma no produto | **Proibido** — zero referência à marca |

## Núcleo do produto

### Saída obrigatória por indicação

Toda recomendação deve incluir:

1. **Cenário pessimista** — entrada conservadora / pior caso plausível
2. **Cenário realista** — linha central baseada no modelo
3. **Cenário otimista** — upside plausível
4. **Explicabilidade** — quais dados e tendências levaram à conclusão (não caixa-preta)

### Variáveis e dimensões de análise (12 meses)

| Dimensão | Exemplos |
|----------|----------|
| Time | Forma, gols marcados/sofridos, clean sheets, falha em marcar |
| Jogador | Estatísticas individuais, titular/reserva, cartões |
| Comportamento | Agressivo vs retranqueiro, intensidade |
| Disciplina | Cartões amarelos e vermelhos por jogo/período |
| Timing | Horários aproximados de gols (marcados e sofridos) |
| Reação | Comportamento após tomar gol vs após fazer gol |
| Competição | Pontos corridos vs copas/mata-mata |
| Contexto | Mandante/visitante, importância do jogo |

### Stack provável (não confirmada)

| Camada | Direção |
|--------|---------|
| Coleta / ETL | Python (requests, scrapers, agendamento) |
| Armazenamento | Supabase (PostgreSQL) |
| Análise / ML | pandas, scikit-learn; evoluir conforme necessidade |
| Apresentação | A definir (web app provável para dashboards) |

## Estratégia de dados

### Fase 1 — APIs públicas gratuitas

| Fonte | Free tier | Cobertura | Limitações |
|-------|-----------|-----------|------------|
| [API-Football](https://www.api-football.com/) | 100 req/dia, todos endpoints | 1200+ ligas; stats, eventos, cartões, lineups | Histórico limitado no free; rate limit |
| [football-data.org](https://www.football-data.org/) | 10 req/min | 12 competições top (incl. Brasileirão, UCL, PL) | Cards/lineups só em planos pagos; scores delayed no free |
| Scraping complementar | — | FBref, Transfermarkt, sites de stats | ToS, fragilidade, manutenção |

### Fase 2 — APIs pagas (quando validado)

- football-data.org Deep Data (~€29/mo) — cartões, escalações, artilheiros
- API-Football Pro — histórico profundo, volume
- Odds dedicadas se necessário

### Fase 3 — Exchange (hipótese)

- Testar leitura de odds/mercados via sessão autenticada
- **Riscos:** ToS, instabilidade, bloqueio de conta — tratar como P&D, não dependência do MVP

## Esporte e competições

| Item | Decisão |
|------|---------|
| Esporte | **Somente futebol** |
| **MVP imediato** | **Copa do Mundo 2026** — extrair valor agora (jun–jul/2026) |
| Liga pós-Copa | Brasileirão Série A (retoma como campeonato base) |
| Modelo futuro de ligas | Perfil do usuário escolhe **1 campeonato** no plano base |
| Planos pagos | Acompanhar **mais de 1 campeonato** por assinatura |
| Usuário root | Conta do fundador com **acesso livre** (sem limite de ligas) |

### Copa 2026 — dados técnicos

| Item | Valor |
|------|-------|
| API-Football | `league=1`, `season=2026` — 104 jogos; events, lineups, injuries, stats |
| football-data.org | World Cup incluído no free tier (12 competições) |
| Particularidade | Seleções ≠ clubes — usar eliminatórias, amistosos recentes, forma dos titulares |

## Filosofia operacional: aversão a zero gols

**Regra de ouro:** nunca adivinhar, nunca presumir. Só indicar jogos onde **os dados** sustentam potencial alto de gols.

| Postura | Significado |
|---------|-------------|
| **FUGIR** | Qualquer cenário onde 0 gols seja plausível nos dados |
| **PAVOR** | Jogos truncados, retranqueiros, must-draw, elenco ofensivo desfalcado |
| **BUSCAR** | Jogos "soltos", times elaborados ofensivamente, defesas permeáveis |

O modelo é **filtro de exclusão primeiro**: descartar tudo que cheire 0-0; só então ranquear o que sobrou para over 0,5 / 1,5.

### Janela diária (decisão de produto — 2026-06-13, atualizado 2026-06-22)

A análise roda **somente nos jogos do dia operacional** (`APP_TIMEZONE`, default `America/Sao_Paulo`). Lesões, suspensões e contexto mudam de um dia pro outro; não faz sentido manter leituras de jogos futuros na home.

**Dia operacional = 06:00 → 06:00** (fuso `America/Sao_Paulo`). O dia não vira à meia-noite: jogos da madrugada (ex.: 00:00–05:59) entram no dia que começou às 6h do dia anterior. Ex.: às 20h de 22/06, a janela inclui Jordânia x Argélia às 00:00 de 23/06.

| Camada | Escopo |
|--------|--------|
| **Perfis das seleções** | Histórico embutido (últimos ~30 jogos FINISHED) — passo 2 atualiza quem joga hoje **+ backfill** de seleções sem perfil válido (não gera leitura) |
| **Filtro + LLM** | Apenas fixtures SCHEDULED/TIMED/IN_PLAY com kickoff na janela 6h→6h do dia operacional |
| **UI** | Home mostra candidatos e descartados de hoje; calendário completo continua no banco via sync |

Futuro: escalações/lesões pré-jogo (API paga ou scraping) entram **no dia do jogo**, antes da leitura LLM.

### Critérios objetivos — exclusão automática *(sem indicar)*

Jogo **não entra** se qualquer condição for verdadeira (limiares calibráveis com dados):

1. **Taxa 0-0** — qualquer seleção com ≥ X% de jogos 0-0 nos últimos 12 meses (elim/atórias + Copa)
2. **Under 0,5 combinado** — soma das médias de gols marcados **abaixo** do piso mínimo definido
3. **Retranca dupla** — ambas com alta taxa de clean sheet **e** baixa média de gols marcados
4. **Desfalque ofensivo crítico** — titular top-N em gols/xG fora; substituto não compensa estatisticamente
5. **Contexto Copa — empate basta** — rodada final de grupo onde empate classifica ambas (incentivo ao 0-0)
6. **Mata-mata — underdog extremo** — favorito claro + underdog com padrão histórico de bloco defensivo
7. **H2H recente** — últimos N confrontos com média de gols abaixo do piso ou alta incidência 0-0

### Critérios objetivos — inclusão *(potencial de gols)*

Jogo **candidato** só se **todos** os obrigatórios passarem:

| # | Critério | Fonte |
|---|----------|-------|
| 1 | Média combinada de gols (marcados + sofridos) ≥ limiar | Últimos 12 meses |
| 2 | Ambas marcam em ≥ Y% dos jogos recentes | Qualificatórias + amistosos |
| 3 | Pelo menos uma defesa permeável (média gols sofridos ≥ Z) | Stats seleção |
| 4 | Elenco ofensivo titular disponível (verificar injuries/lineups) | API injuries + escalação |
| 5 | Histórico over 0,5 ≥ W% nos jogos analisados | Calculado |
| 6 | Explicabilidade completa — cada critério com valor numérico | Saída do modelo |

### Checklist pré-jogo (Copa)

```
- [ ] Escalação confirmada ou provável (24h antes)
- [ ] Desfalques: artilheiros, criadores, laterais ofensivos
- [ ] Suspensões (acumulados amarelos)
- [ ] Stakes do jogo (precisa vencer vs empate basta)
- [ ] Fase (grupos vs mata-mata)
- [ ] Histórico H2H e forma recente (últimos 5–10 jogos)
- [ ] Score de potencial de gols calculado — passou no filtro?
```

## Mercados de aposta (preferências do fundador)

Foco principal: **gols** — buscar jogos com 1 ou mais gols. **Rejeitar** qualquer indício de jogo sem gol.

| Mercado | Uso | Risco / notas |
|---------|-----|---------------|
| **Over 0,5** | Filial conservadora — 1+ gol | MVP Copa; volume maior |
| **Over 1,5** | Filial intermediária — 2+ gols | MVP Copa; mais seletiva |
| **Favorito (match odds)** | Quando odd ≥ ~1,50 e cenário **muito óbvio** | Depois da validação gols |
| **Resultado correto (lay)** | Apostar **contra** placar exato | Alto risco; depois |

> Mercados secundários e novas filiais entram conforme validação de retorno.

## Conceito de Filiais *(a amadurecer)*

Cada **tipo de entrada** opera como uma **filial** — uma "empresa" independente dentro do sistema, com P&L e métricas próprias.

**Exemplos iniciais:**

| Filial | Mercado | Papel |
|--------|---------|-------|
| `over_0_5` | Over 0,5 gols | Entrada conservadora em gols |
| `over_1_5` | Over 1,5 gols | Linha intermediária |
| `favorite_obvious` | Vitória do favorito (odd ≥ 1,50) | Só quando modelo indica cenário óbvio |
| `lay_correct_score` | Lay em resultado correto | Alto risco; tracking separado |

**O que cada filial deve rastrear** *(definir detalhes depois)*:

- Indicações emitidas vs. resolvidas (green/red)
- ROI e yield por período
- Confiança média do modelo vs. taxa de acerto
- Comparativo entre filiais — "quem dá retorno e quem não dá"

**Princípio:** filiais não compartilham bankroll na lógica de negócio — cada uma tem carteira virtual para medir performance isolada.

## MVP — Copa do Mundo 2026 *(prioridade atual)*

| Feature | Prioridade | Notas |
|---------|------------|-------|
| Ingestão seleções — 12 meses (elim + amistosos) | MVP | Base para perfil; API `league=1` |
| Fixtures Copa 2026 — calendário completo | MVP | 104 jogos; cache local |
| Filtro anti-zero-gols (exclusão) | MVP | **Core** — descartar antes de ranquear |
| Score de potencial de gols | MVP | Ranquear candidatos restantes |
| Verificação desfalques / injuries | MVP | Titular fora = recalcular ou excluir |
| Filiais **over_0_5** e **over_1_5** | MVP | Ambas no MVP Copa |
| 3 cenários + explicabilidade por jogo | MVP | Mostrar cada critério numérico |
| Tracking P&L por filial | MVP | Validar na Copa |
| Usuário root | MVP | Fundador |
| Brasileirão Série A | Pós-Copa | Retoma como liga base do produto |
| Filiais favorito / lay correct score | Depois | Após Copa |
| Odds exchange | Depois | P&D sessão browser |

## Restrições conhecidas

- Repositório greenfield; sem commits ainda.
- **Sem marca Betfair** em qualquer superfície do produto.
- Validação privada — não publicar picks até ter histórico de acerto.
- API oficial da exchange descartada por custo.
- 100 req/dia (API-Football free) exige cache agressivo e ingestão batch.

## Decisões tomadas

| Data | Decisão | Motivo |
|------|---------|--------|
| 2026-06-13 | Modo descoberta | Repo vazio |
| 2026-06-13 | Skill de projeto criada | Estruturar contexto |
| 2026-06-13 | Produto = analytics preditivo explicável | Visão do fundador |
| 2026-06-13 | Três cenários por indicação | Requisito de produto |
| 2026-06-13 | Histórico mínimo 12 meses | Base estatística |
| 2026-06-13 | APIs públicas primeiro | Custo zero para validar |
| 2026-06-13 | Sem nome Betfair no produto | Evitar conflito de marca |
| 2026-06-13 | Público = só fundador no MVP | Validar antes de expor |
| 2026-06-13 | API oficial exchange descartada | Custo/benefício |
| 2026-06-13 | Esporte = somente futebol | Escopo fechado |
| 2026-06-13 | Liga piloto = Brasileirão Série A | Campeonato nacional 1ª divisão |
| 2026-06-13 | Plano base = 1 campeonato; mais = pago | Modelo freemium futuro |
| 2026-06-13 | Usuário root com acesso livre | Conta do fundador |
| 2026-06-13 | Mercados foco = gols (over), favorito óbvio, lay correct score | Preferência operacional |
| 2026-06-13 | Conceito de filiais por tipo de entrada | A amadurecer; tracking P&L isolado |
| 2026-06-13 | **MVP = Copa do Mundo 2026** | Oportunidade imediata; torneio em andamento |
| 2026-06-13 | Filiais MVP = over_0_5 + over_1_5 | Ambas na Copa |
| 2026-06-13 | Filosofia anti-zero-gols | Exclusão por dados; nunca presumir |
| 2026-06-13 | Desfalque ofensivo = critério de exclusão | Verificar titular fora antes de indicar |
| 2026-06-13 | Nome produto: **Palpitaria FC** | Domínio palpitariafc.com.br |
| 2026-06-13 | Stack: FastAPI + HTMX + Supabase + OpenRouter | Cloud Run depois; mobile via REST |
| 2026-06-13 | Dados: football-data.org v4 (`WC`) | Token do fundador; free tier |
| 2026-06-13 | LLM MVP = Narrador Brasileiro | Ginga e leitura de jogo; via OpenRouter |
| 2026-06-13 | LLM via OpenRouter + OpenAI SDK | Mesmo padrão SpeakFlow; `OPENAI_API_KEY` sk-or- |
| 2026-06-13 | Modelo default | `google/gemini-2.0-flash-001` via OpenRouter |
| 2026-06-13 | Fix: UNIQUE constraint teams.external_id | Habilitado autoflush no SQLAlchemy para evitar duplicatas na ingestão |

## Decisões pendentes

- [ ] Limiares numéricos do filtro (calibrar com dados reais das seleções)
- [ ] Formato do app (web dashboard vs CLI vs notebook)
- [x] Banco de dados — Supabase (PostgreSQL)
- [ ] API key API-Football (fundador registrar free tier)
- [ ] Viabilidade de acesso via sessão à exchange
- [ ] Nome do produto (working title)
- [ ] Detalhamento do modelo de filiais (bankroll, stake, métricas)

## Notas da conversa

### Sessão 1 — 2026-06-13

- Exploração de contexto antes de código.
- Skill inicial criada.

### Sessão 2 — 2026-06-13

- Modelo preditivo com máximo de variáveis; Python + análise de dados.
- Explicabilidade obrigatória; 3 cenários (pessimista/realista/otimista).
- 12 meses histórico por time e jogador; tendências comportamentais.
- APIs públicas primeiro; pagas depois; scraping como complemento.
- Exchange = plataforma pessoal; API oficial inviável; testar sessão browser.
- Validação solo; sem marca Betfair no produto.

### Sessão 3 — 2026-06-13

- Esporte fechado: futebol.
- Liga piloto: Brasileirão Série A; futuro = 1 campeonato no plano base, mais ligas = pago.
- Usuário root (fundador) com acesso livre a todas as ligas.
- Mercados: gols (over 0,5 / 1,5+), favorito quando odd ≥ 1,50 e cenário óbvio, lay em resultado correto.
- Conceito de **filiais** — cada tipo de entrada como unidade de negócio com P&L próprio; amadurecer depois.

### Sessão 4 — 2026-06-13

- **MVP pivot:** Copa do Mundo 2026 — extrair valor agora.
- Filiais over 0,5 e 1,5 no MVP.
- Filosofia: **fugir de zero gols** — filtro de exclusão por dados, nunca adivinhar.
- Buscar jogos "soltos", ofensivos; verificar desfalque do melhor jogador.
- Brasileirão fica pós-Copa.

## Glossário do projeto

| Termo | Significado neste app |
|-------|----------------------|
| Indicação | Recomendação de entrada derivada do modelo (não "palpite") |
| Cenário pessimista | Entrada conservadora; premissas desfavoráveis |
| Cenário realista | Linha central do modelo |
| Cenário otimista | Upside plausível dentro dos dados |
| Explicabilidade | Rastreio das variáveis e tendências que sustentam cada cenário |
| Perfil de time | Agregado estatístico/comportamental dos últimos 12 meses |
| Filial | Unidade de negócio por tipo de mercado (ex.: over_0_5); P&L e métricas isoladas |
| Lay | Apostar contra um resultado (ex.: lay em placar exato) |
| Favorito óbvio | Favorito com odd ≥ ~1,50 e confiança estatística muito alta |
| Filtro anti-zero-gols | Regras de exclusão — jogo não indicado se 0 gols for plausível nos dados |
| Score de potencial de gols | Métrica composta para ranquear jogos que passaram no filtro |
| Desfalque crítico | Titular top ofensivo ausente; impacto quantificado na projeção de gols |
