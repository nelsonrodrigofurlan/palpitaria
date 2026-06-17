from datetime import datetime

from palpitaria.services.analyzer import FixtureAnalysis
from palpitaria.services.explainer import (
    _compose_explanation_from_analysis,
    _explanation_quality_issues,
    _finalize_explanation,
    _is_acceptable_explanation,
    _strip_markdown_noise,
)

BROKEN_LLM_SAMPLE = (
    "*referee, climate:* Al Jassim mentioned. Climate not found (omitted or mentioned as neutral).\n"
    "* *Historical Web + API:* Mentioned recent history (Over 1.5, combined average of 3.75).\n"
    "* *Best pick grounded:* Vitória de Portugal grounded with stats, backstage, referee.\n"
    "*No"
)

GOOD_PT_SAMPLE = (
    "Portugal chega com média elevada de gols nos últimos jogos e defesa que concede espaço. "
    "O histórico híbrido confirma tendência de jogos abertos, reforçado pelos bastidores do dia.\n\n"
    "A leitura aponta vitória portuguesa com jogo movimentado: o árbitro costuma deixar fluir. "
    "A exposição em Over 1.5 ganha suporte nos números combinados.\n\n"
    "Risco: se o Congo fechar no primeiro tempo, o fluxo de gols pode atrasar."
)


def test_rejects_broken_english_checklist():
    assert not _is_acceptable_explanation(BROKEN_LLM_SAMPLE)
    issues = _explanation_quality_issues(BROKEN_LLM_SAMPLE)
    assert "ingles_ou_vazamento_prompt" in issues
    assert "termino_incompleto" in issues or "sem_frase_final" in issues


def test_accepts_portuguese_prose():
    assert _is_acceptable_explanation(GOOD_PT_SAMPLE)


def test_strip_markdown_noise():
    cleaned = _strip_markdown_noise(BROKEN_LLM_SAMPLE)
    assert "*" not in cleaned
    assert "Historical Web" in cleaned  # conteúdo permanece para fallback/retry


def test_finalize_truncates_on_sentence_boundary():
    long_text = "A" * 200 + ". " + "B" * 2000 + "."
    out = _finalize_explanation(long_text, max_chars=300)
    assert len(out) <= 300
    assert out.endswith((".", "…"))


def test_compose_fallback_in_portuguese():
    analysis = FixtureAnalysis(
        fixture_id=1,
        external_id=1,
        home_name="Portugal",
        away_name="RD Congo",
        home_crest=None,
        away_crest=None,
        utc_date=datetime(2026, 6, 17, 17, 0, 0),
        status="TIMED",
        stage="GROUP_STAGE",
        group_name="GROUP_K",
        goal_potential_score=100,
        excluded=False,
        exclusion_reasons=[],
        criteria=[],
        best_pick={
            "market": "VITÓRIA: Portugal",
            "verdict": "STRONG",
            "reason": "Favorito com média ofensiva superior e adversário vulnerável.",
            "web_factor": "Histórico recente confirma jogos abertos do favorito.",
        },
        match_context={"referee": "Al Jassim", "weather": "Calor", "pitch": "Natural"},
    )
    text = _compose_explanation_from_analysis(analysis)
    assert "Portugal" in text
    assert "VITÓRIA" in text
    assert _is_acceptable_explanation(text)
