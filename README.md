# Palpitaria FC ⚽

Análise preditiva de futebol.

Focada em extrair valor da **Copa do Mundo 2026** através de uma leitura fundamentada (filtro anti-zero-gols) e explicável via IA.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
copy .env.example .env
```

Edite o arquivo `.env` com seu `FOOTBALL_DATA_TOKEN` e `OPENAI_API_KEY` (OpenRouter).

## Como Rodar

```bash
uvicorn palpitaria.main:app --reload
```

Acesse: http://127.0.0.1:8000

## Fluxo de Trabalho

1.  **Sincronizar Copa:** Puxa os jogos e perfis das seleções do `football-data.org`.
2.  **Gerar Leituras:** Aplica o filtro de exclusão (anti-zero-gols) e gera a narração da IA para os candidatos.

## Identidade Visual

As artes conceituais estão em `src/palpitaria/static/assets/`:
- `logo.png`
- `logo_soul.png`
- `icon.png`

---
*Domínio oficial: palpitariafc.com.br*
