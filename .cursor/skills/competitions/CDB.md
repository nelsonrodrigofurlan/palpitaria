# Especialista: Copa do Brasil (CDB)

Este skill define o comportamento do Palpitaria FC para a Copa do Brasil.

## Fonte de dados
- **Oficial:** tabela CBF `https://www.cbf.com.br/futebol-brasileiro/tabelas/copa-do-brasil/masculino/{ano}`
- Código: `services/cbf_cdb_ingest.py` — sync automático quando football-data retorna 403
- Clubes reaproveitam perfis da Série A/B quando existirem

## Características do Campeonato
- **Mata-Mata**: Jogos de ida e volta (ou jogo único no início).
- **Clima eliminatório**: Underdog fecha; favorito não ganha fácil no 1º tempo; após gol a partida muda (ver `knockout_climate.py`).
- **Regulamento**: Gol fora de casa (verificar se vigente), pressão por resultado imediato.
- **Zebras**: Times menores costumam jogar a "vida" contra gigantes.

## Regras de Análise (CDB)
1. **Motivação e Rotação**: Verificar se o time grande está poupando jogadores para a Libertadores ou Brasileirão.
2. **Contexto do Placar**: No jogo de volta, o resultado da ida dita o ritmo (ex: se o favorito ganhou de 3-0 na ida, pode jogar em ritmo lento na volta).
3. **Mando de Campo**: Estádios menores ou gramados ruins no interior podem nivelar o jogo.

## Prioridades de Mercado (CDB)
- **Over 0.5 / 1.5**: Em jogos onde um time precisa desesperadamente do gol.
- **Vencedor (1X2)**: Cuidado com favoritos fora de casa em estádios hostis.
- **Lay 0-0**: Risco alto em jogos de ida muito truncados.
