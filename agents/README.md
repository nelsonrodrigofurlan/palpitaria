# Agentes — Palpitaria FC

Pasta de **contratos de agente** no padrão do módulo 4 (POS): identidade, regras, habilidades, ciclo perceber→planejar→agir→avaliar.

> Regras de negócio (filosofia anti-zero-gols, mercados, campeonatos) vivem em [`.cursor/skills/betfair/SKILL.md`](../.cursor/skills/betfair/SKILL.md) — é a fonte de verdade do domínio. Aqui só o contrato de **execução** do agente. Se uma política mudar em um dos dois lados, replicar no outro.

| Agente | Tipo | Papel |
|--------|------|--------|
| [`palpitaria-diario/`](palpitaria-diario/) | `task_based` (schedule → `autonomous`) | Rotina do dia: sync → análise com gate de fundamento → rascunho de alerta → histórico IA |

**Runtime:** `python -m palpitaria.agents` (pacote `src/palpitaria/agents/`).

```bash
python -m palpitaria.agents validar
python -m palpitaria.agents rodar --comps BSA,BSB                 # planejador LLM
python -m palpitaria.agents rodar --comps BSA,BSB --planejador fixed
python -m palpitaria.agents rascunho --comps BSA,BSB --sem-narrar
```

Ciclo: perceber → planejar (`llm` ou `fixed`) → agir → avaliar. Tools: sync → análise (fundamento) → histórico IA → rascunho. `publicar` é opcional + confirmação.

Sem `OPENAI_API_KEY`, o planejador `llm` cai automaticamente na ordem fixa.

**Regra de ouro:** agente **não publica** palpite sem aprovação humana. Draft = ok. Homologação = humana.
