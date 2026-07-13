# Especialista: Brasileirão Série B (BSB)

Este skill define o comportamento do Palpitaria FC para a Série B.

## Características do Campeonato
- **Pontos Corridos**: 38 rodadas; calendário longo.
- **Mando de campo forte**: estádios hostis e viagens longas — home edge maior que na Série A.
- **Variância alta**: elenco irregular, zebras frequentes, menos liquidez na exchange.
- **Gols**: média costuma ser um pouco menor / jogos mais truncados que a elite da A.

## Regras de Análise (BSB)
1. **Amostra**: mínimo **5 jogos** recentes para STRONG (igual BSA).
2. **API first**: football-data.org `BSB` — web só para desfalques/notícias.
3. **Edge mais exigente**: homologar só com margem maior (mercado menos eficiente, mas modelo menos calibrado no início).
4. **Over 1.5** como core; Over 2.5 raro e seletivo.
5. **1X2 fora de casa**: desconfiar de favorito visitante — preferir handicap/gols.

## Prioridades de Mercado (BSB)
- **Over 1.5 / Over 0.5**: base homologada.
- **Handicap** do mandante quando ML espremido.
- **Lay 0-0**: só com defesas frágeis + modelo com P(0-0) baixo.

## Código
- Perfil: `services/competitions.py` → `BSB`
- Predição: `services/prediction.py` (Poisson + home_advantage 0.32)
