# Agentes — Palpitaria FC

Pasta de **contratos de agente** no padrão do módulo 4 (POS): identidade, regras, habilidades, ciclo perceber→planejar→agir→avaliar.

| Agente | Tipo | Papel |
|--------|------|--------|
| [`palpitaria-diario/`](palpitaria-diario/) | `task_based` (schedule → `autonomous`) | Rotina do dia: sync → análise com gate de fundamento → rascunho de alerta → histórico IA |

**Runtime mínimo:** `python -m palpitaria.agents` (pacote `src/palpitaria/agents/`).

```bash
python -m palpitaria.agents validar
python -m palpitaria.agents rodar --comps BSA,BSB
python -m palpitaria.agents rascunho --comps BSA,BSB --sem-narrar
```

Ciclo `rodar` (task_based, ordem fixa — sem LLM no planejador ainda): sync → analisar → histórico IA → rascunho. `publicar` é opcional e pedirá confirmação.

**Regra de ouro:** agente **não publica** palpite sem aprovação humana. Draft = ok. Homologação = humana.
