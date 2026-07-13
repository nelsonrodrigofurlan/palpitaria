# Especialista: Copa do Mundo (WC)

Este skill define o comportamento do Palpitaria FC para a Copa do Mundo.

## Características do Campeonato
- **Curto e Intenso**: Máximo de 7 jogos por seleção.
- **Alta Volatilidade**: O "momento" e o clima pesam mais que o histórico de 2 anos atrás.
- **Escalações Dinâmicas**: Mudanças rápidas entre jogos da fase de grupos e mata-mata.

## Regras de Análise (WC)
1. **Amostra Flexível**: Aceitar análise com apenas **1 jogo** de histórico (na estreia, usar dados de amistosos/eliminatórias via web).
2. **Perfil Híbrido Obrigatório**: Sempre cruzar dados da API com pesquisa web (Step 3).
3. **Fator "Estreia"**: No primeiro jogo, a confiança deve ser moderada, a menos que o favorito seja esmagador.
4. **Mata-Mata**: Ver regra global em `SKILL.md` (princípio 0) e `services/knockout_climate.py`.
   - Pré-live: Over 1.5 > Over 2.5; handicap e leitura live após o placar abrir.
   - Explicar prorrogação/pênaltis (mercado = tempo regulamentar).
   - Cenários live: 0-0 HT fechado; 2º tempo 0-0 chato; favorito abre → elástico; zebra abre → bloco atrás.

## Prioridades de Mercado (WC)
- **Over 0.5 / 1.5**: Foco total em gols, especialmente em jogos de seleções ofensivas.
- **Lay 0-0**: Excelente para jogos de seleções equilibradas onde o empate não serve para ninguém.
- **Vencedor (1X2)**: Apenas se houver disparidade técnica massiva fundamentada em dados recentes.
