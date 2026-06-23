"""Inteligência coletiva — diálogo com contexto das análises e propostas do usuário."""

from __future__ import annotations

import json
import unicodedata
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from palpitaria.config import settings
from palpitaria.models import Competition, Fixture, FixtureReport, Team, UserInsight
from palpitaria.services.ingest import latest_profile
from palpitaria.services.llm_client import chat_completion
from palpitaria.services.llm_utils import _parse_json_from_llm
from palpitaria.services.team_names import ENGLISH_ALIASES, localize_team_name, names_for_matching

CHAT_HISTORY_DAYS = 2
CHAT_DAILY_LIMIT = 3
CHAT_ADMIN_EMAIL = "nelson.r.furlan@gmail.com"
BANTER_MIN_AGE_HOURS = 2

COLLECTIVE_SYSTEM_PROMPT = """Você é o analista colaborativo da Palpitaria FC no chat de Inteligência Coletiva.

## REGRA ABSOLUTA — PALPITE OFICIAL IMUTÁVEL
O palpite oficial do produto (homologada / alternativa na home e no pipeline) **NUNCA** muda por esta conversa.
- Você **não** emite novo palpite oficial, **não** substitui e **não** "homologa" entrada do usuário no chat.
- Mesmo que os dados apoiem a ideia do usuário (`verdict: supports`), deixe explícito: isso é **leitura colaborativa**, não alteração do palpite do sistema.
- O pipeline (skill do produto: foco em gols, anti-zero, descarte) é soberano. Este chat **explica** e **aprofunda** — não reescreve picks.

## O que você faz
1. **Explicar** — Por que homologamos, descartamos ou sugerimos algo no pacote (critérios, scores, leitura IA).
2. **Bate-papo adulto** — Leia a proposta do usuário, entenda e **opine com honestidade** (a favor ou contra), com números + odds. **Não é para só concordar** — se os dados contradizem, diga. Isso **não altera** o palpite pré-live já homologado pelo pipeline.
3. **Incorporar fatos** — Só `incorporate: true` para **fato técnico verificável** (lesão confirmada, bastidor com fonte, dado de campo) que enriqueça análises **futuras** do time — nunca por proposta de aposta do usuário.

## Papinho, propostas e palpite firme
- **Papinho / feeling / entrada não homologada:** fundamente com o pacote; **opine** (apoie ou critique); **não homologue pré-live** — você não sugeriu aquela entrada oficialmente.
- **Pedem palpite firme** ("entra ou não?"): **escapada à francesa** — analise o cenário e oriente a **acompanhar como TRADER durante o jogo** (live/in-play), não como pré-live. Pré-live fica com o pipeline.

## Pilares do skill (não desvie)
- Foco em gols; homologada vs alternativa; liberdade de descarte; cautela e sobriedade.
- Proposta do usuário: responda com análise honesta, sem hype de torcida.
- `incorporate: true` **proibido** em `insight_type: proposal` — proposta nunca vira base só porque você achou ok.
- Nunca invente placares, lesões ou odds fora do pacote/fontes web.
- Português do Brasil, 2–4 parágrafos em `response`. Sem markdown, sem bullets.

## Humanização (banter opcional)
Se o pacote trouxer `banter_hook`, pode soltar **no máximo UMA** piada/zueira leve no início ou no fim — ex. palpite deu GREEN, seria RED, chegou a entrar? Tom de parça, não robô.
- **Só use** se couber naturalmente na resposta atual; se não couber, **ignore** o hook.
- **Nunca** repita a mesma zueira em mensagens seguidas; não comente histórico em toda resposta.
- O hook é sobre conversa **recente e relevante** — não force.

## Veredito sobre proposta do usuário (`verdict`) — só orientação no chat
- `supports` — dados sustentam a ideia (deixe claro: não é palpite oficial)
- `neutral` — inconclusivo ou fora do foco de gols
- `caution` — arriscado ou contra filtro anti-zero
- `against` — dados contradizem
- `n/a` — não era proposta de entrada

Retorne SOMENTE JSON válido:
{
  "response": "texto completo para o usuário",
  "incorporate": true|false,
  "incorporate_reason": "frase curta se incorporate true (só fato), senão vazio",
  "identified_team_id": null|int,
  "identified_fixture_id": null|int,
  "insight_type": "question|proposal|fact|general",
  "verdict": "supports|neutral|caution|against|n/a"
}
"""


def _normalize_text(text: str) -> str:
    lowered = (text or "").lower().strip()
    nfkd = unicodedata.normalize("NFKD", lowered)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def chat_history_since() -> datetime:
    return datetime.utcnow() - timedelta(days=CHAT_HISTORY_DAYS)


def fetch_user_chat_history(db: Session, user_id: int, *, ascending: bool = True) -> list[UserInsight]:
    since = chat_history_since()
    order = UserInsight.created_at.asc() if ascending else UserInsight.created_at.desc()
    return (
        db.query(UserInsight)
        .filter(UserInsight.user_id == user_id, UserInsight.created_at >= since)
        .order_by(order)
        .all()
    )


def is_chat_admin(email: str | None) -> bool:
    return (email or "").strip().lower() == CHAT_ADMIN_EMAIL


def count_user_messages_operational_day(db: Session, user_id: int) -> int:
    from palpitaria.services.analyzer import get_today_context

    ctx = get_today_context()
    return (
        db.query(UserInsight)
        .filter(
            UserInsight.user_id == user_id,
            UserInsight.created_at >= ctx.start_utc,
            UserInsight.created_at < ctx.end_utc,
        )
        .count()
    )


def user_chat_daily_quota(db: Session, user_id: int, user_email: str | None = None) -> dict[str, Any]:
    if is_chat_admin(user_email):
        return {
            "limited": False,
            "used": 0,
            "limit": None,
            "remaining": None,
            "blocked": False,
            "operational_day": None,
        }
    from palpitaria.services.analyzer import get_today_context

    ctx = get_today_context()
    used = count_user_messages_operational_day(db, user_id)
    remaining = max(0, CHAT_DAILY_LIMIT - used)
    return {
        "limited": True,
        "used": used,
        "limit": CHAT_DAILY_LIMIT,
        "remaining": remaining,
        "blocked": used >= CHAT_DAILY_LIMIT,
        "operational_day": ctx.label,
    }


def _relative_day_label(when_utc: datetime) -> str:
    tz = ZoneInfo(settings.app_timezone)
    local_now = datetime.now(tz).date()
    local_day = when_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz).date()
    delta = (local_now - local_day).days
    if delta <= 0:
        return "hoje"
    if delta == 1:
        return "ontem"
    return "anteontem"


def _score_user_proposal(insight: UserInsight, fixture: Fixture) -> str | None:
    """Inferência simples: green ou red para zueira pós-jogo. None se não der para cravar."""
    if fixture.home_score is None or fixture.away_score is None:
        return None
    hs, aws = fixture.home_score, fixture.away_score
    total = hs + aws
    content = _normalize_text(insight.content)
    raw = insight.content or ""

    home = localize_team_name(fixture.home_team.name, fixture.home_team.external_id)
    away = localize_team_name(fixture.away_team.name, fixture.away_team.external_id)
    home_n, away_n = _normalize_text(home), _normalize_text(away)

    if "over" in content or "gol" in content:
        if "2.5" in raw or "25" in content:
            return "green" if total >= 3 else "red"
        if "1.5" in raw or "15" in content:
            return "green" if total >= 2 else "red"
        if "0.5" in raw or "05" in content:
            return "green" if total >= 1 else "red"

    home_in = home_n in content
    away_in = away_n in content
    if not home_in and not away_in:
        return None

    win_words = ("vitoria", "vencer", "ganhar", "win", "favorit", "pagando", "entrada", "odd", "%")
    has_win_intent = insight.insight_type == "proposal" or insight.verdict == "supports"
    has_win_intent = has_win_intent or any(w in content for w in win_words)
    if not has_win_intent:
        return None

    if home_in and not away_in:
        side = "home"
    elif away_in and not home_in:
        side = "away"
    else:
        side = "home" if content.index(home_n) <= content.index(away_n) else "away"

    if side == "home":
        if hs > aws:
            return "green"
        return "red"
    if aws > hs:
        return "green"
    return "red"


def build_banter_hook(db: Session, user_id: int) -> dict[str, Any] | None:
    """Uma zueira candidata — palpite recente do usuário com jogo já encerrado."""
    since = chat_history_since()
    min_created = datetime.utcnow() - timedelta(hours=BANTER_MIN_AGE_HOURS)
    insights = (
        db.query(UserInsight)
        .options(
            joinedload(UserInsight.fixture).joinedload(Fixture.home_team),
            joinedload(UserInsight.fixture).joinedload(Fixture.away_team),
        )
        .filter(
            UserInsight.user_id == user_id,
            UserInsight.created_at >= since,
            UserInsight.created_at <= min_created,
            UserInsight.fixture_id.isnot(None),
        )
        .order_by(UserInsight.created_at.desc())
        .limit(20)
        .all()
    )
    for row in insights:
        fx = row.fixture
        if not fx or fx.status not in ("FINISHED", "AWARDED"):
            continue
        outcome = _score_user_proposal(row, fx)
        if outcome not in ("green", "red"):
            continue
        if row.insight_type not in ("proposal", "general", "question") and row.verdict not in (
            "supports",
            "caution",
        ):
            continue
        home = localize_team_name(fx.home_team.name, fx.home_team.external_id)
        away = localize_team_name(fx.away_team.name, fx.away_team.external_id)
        return {
            "match": f"{home} x {away}",
            "user_said": (row.content or "")[:140],
            "outcome": outcome,
            "when": _relative_day_label(row.created_at),
            "score": f"{fx.home_score}-{fx.away_score}",
            "verdict_then": row.verdict,
        }
    return None


def teams_mentioned_in_message(db: Session, message: str) -> list[Team]:
    norm_msg = _normalize_text(message)
    if not norm_msg:
        return []
    found: list[Team] = []
    seen: set[int] = set()
    for team in db.query(Team).all():
        if team.id in seen:
            continue
        for variant in names_for_matching(team.name, team.external_id):
            if len(variant) < 3:
                continue
            if variant in norm_msg or variant.replace(" ", "") in norm_msg.replace(" ", ""):
                found.append(team)
                seen.add(team.id)
                break
        if team.id in seen:
            continue
        pt = localize_team_name(team.name, team.external_id)
        for en_key, pt_name in ENGLISH_ALIASES.items():
            if pt_name == pt and len(en_key) >= 3 and en_key in norm_msg:
                found.append(team)
                seen.add(team.id)
                break
    return found


def find_relevant_fixtures(db: Session, teams: list[Team], *, limit: int = 2) -> list[Fixture]:
    from palpitaria.services.analyzer import get_today_context

    if not teams:
        return []
    team_ids = {t.id for t in teams}
    ctx = get_today_context()
    end = ctx.end_utc + timedelta(days=14)
    base = (
        db.query(Fixture)
        .options(joinedload(Fixture.home_team), joinedload(Fixture.away_team))
        .filter(Fixture.utc_date >= ctx.start_utc)
        .filter(Fixture.utc_date < end)
        .filter(Fixture.status.in_(["SCHEDULED", "TIMED", "IN_PLAY", "FINISHED"]))
    )
    if len(team_ids) >= 2:
        matched = (
            base.filter(Fixture.home_team_id.in_(team_ids))
            .filter(Fixture.away_team_id.in_(team_ids))
            .order_by(Fixture.utc_date)
            .limit(limit)
            .all()
        )
        if matched:
            return matched
    fixtures = (
        base.filter(
            or_(Fixture.home_team_id.in_(team_ids), Fixture.away_team_id.in_(team_ids))
        )
        .order_by(Fixture.utc_date)
        .limit(limit)
        .all()
    )
    return fixtures


def _profile_summary(db: Session, team_id: int, team_name: str) -> dict[str, Any]:
    profile = latest_profile(db, team_id)
    if not profile:
        return {"name": team_name, "ready": False}
    raw = json.loads(profile.raw_json or "{}")
    return {
        "name": team_name,
        "ready": True,
        "matches_sampled": profile.matches_sampled,
        "avg_goals_scored": profile.avg_goals_scored,
        "avg_goals_conceded": profile.avg_goals_conceded,
        "zero_zero_rate": profile.zero_zero_rate,
        "over_05_rate": profile.over_05_rate,
        "over_15_rate": profile.over_15_rate,
        "win_rate": profile.win_rate,
        "source": raw.get("source"),
    }


def _report_summary(report: FixtureReport | None) -> dict[str, Any] | None:
    if report is None:
        return None
    best_pick = json.loads(report.best_pick_json) if report.best_pick_json else None
    return {
        "analyzed_at": report.analyzed_at.isoformat() if report.analyzed_at else None,
        "excluded": report.excluded,
        "exclusion_reasons": json.loads(report.exclusion_reasons_json or "[]"),
        "goal_potential_score": report.goal_potential_score,
        "best_pick": best_pick,
        "llm_explanation": (report.llm_explanation or "")[:1200],
        "criteria": json.loads(report.criteria_json or "[]")[:8],
    }


def _odds_for_match(
    db: Session, home_name: str, away_name: str, competition_code: str
) -> dict[str, Any] | None:
    comp = db.query(Competition).filter_by(code=competition_code).first()
    if not comp or not comp.odds_json:
        return None
    try:
        games = json.loads(comp.odds_json)
    except json.JSONDecodeError:
        return None
    home_n = _normalize_text(home_name)
    away_n = _normalize_text(away_name)
    for game in games:
        gh = _normalize_text(game.get("home_team", ""))
        ga = _normalize_text(game.get("away_team", ""))
        if (gh == home_n and ga == away_n) or (gh == away_n and ga == home_n):
            markets = game.get("betfair_ex") or []
            slim = []
            for mkt in markets:
                slim.append(
                    {
                        "key": mkt.get("key"),
                        "outcomes": [
                            {"name": o.get("name"), "price": o.get("price")}
                            for o in (mkt.get("outcomes") or [])
                        ],
                    }
                )
            return {"home": game.get("home_team"), "away": game.get("away_team"), "markets": slim}
    return None


def _message_suggests_deep_dive(message: str) -> bool:
    norm = _normalize_text(message)
    tokens = (
        "odd",
        "odds",
        "entrada",
        "apost",
        "lay",
        "over",
        "under",
        "vitoria",
        "empate",
        "paga",
        "pagando",
        "%",
        "bacana",
        "faz sentido",
        "vale",
        "mercado",
    )
    return any(t in norm for t in tokens)


def _fetch_web_snippets_for_fixture(fixture: Fixture) -> str:
    from palpitaria.services.scraper import get_match_context_queries, get_search_queries, search_web_stalking

    home = fixture.home_team
    away = fixture.away_team
    queries = get_match_context_queries(
        home.name,
        away.name,
        home_external_id=home.external_id,
        away_external_id=away.external_id,
    )
    queries += get_search_queries(home.name, external_id=home.external_id)[:2]
    queries += get_search_queries(away.name, external_id=away.external_id)[:2]
    return search_web_stalking(queries, max_results_per_query=3, min_total_len=60)


def build_chat_context(db: Session, message: str, user_id: int | None) -> dict[str, Any]:
    from palpitaria.services.analyzer import analyze_fixture, get_today_context

    teams = teams_mentioned_in_message(db, message)
    fixtures = find_relevant_fixtures(db, teams)
    ctx = get_today_context()

    fixture_bundles: list[dict[str, Any]] = []
    for fixture in fixtures:
        home = fixture.home_team
        away = fixture.away_team
        home_name = localize_team_name(home.name, home.external_id)
        away_name = localize_team_name(away.name, away.external_id)
        report = db.query(FixtureReport).filter_by(fixture_id=fixture.id).first()
        live = analyze_fixture(db, fixture)
        fixture_bundles.append(
            {
                "fixture_id": fixture.id,
                "match": f"{home_name} x {away_name}",
                "kickoff_utc": fixture.utc_date.isoformat() if fixture.utc_date else None,
                "status": fixture.status,
                "competition": fixture.competition_code,
                "saved_report": _report_summary(report),
                "current_engine": {
                    "excluded": live.excluded,
                    "exclusion_reasons": live.exclusion_reasons,
                    "goal_potential_score": live.goal_potential_score,
                    "best_pick": live.best_pick,
                    "strong_criteria": live.strong_criteria_count,
                    "criteria_brief": live.criteria_brief,
                },
                "home_profile": _profile_summary(db, home.id, home_name),
                "away_profile": _profile_summary(db, away.id, away_name),
                "odds": _odds_for_match(db, home_name, away_name, fixture.competition_code),
            }
        )

    web_snippets = ""
    if fixtures and _message_suggests_deep_dive(message):
        try:
            web_snippets = _fetch_web_snippets_for_fixture(fixtures[0])[:3500]
        except Exception:
            web_snippets = ""

    prior = []
    if user_id:
        for row in fetch_user_chat_history(db, user_id, ascending=False)[:8]:
            prior.append(
                {
                    "you_said": row.content[:200],
                    "verdict": row.verdict,
                    "when": _relative_day_label(row.created_at),
                }
            )

    banter_hook = build_banter_hook(db, user_id) if user_id else None

    return {
        "operational_day": ctx.label,
        "mentioned_teams": [localize_team_name(t.name, t.external_id) for t in teams],
        "fixtures": fixture_bundles,
        "web_snippets": web_snippets or None,
        "recent_chat": prior,
        "banter_hook": banter_hook,
        "product_focus": "Mercados de gols (Over) homologados; 1X2 e Lay CS como alternativas.",
        "official_picks_immutable": (
            "Palpites da home/pipeline não mudam por este chat. "
            "Respostas aqui são orientação; homologação só via pipeline."
        ),
    }


def _resolve_incorporate(parsed: dict) -> bool:
    """Chat nunca altera palpite oficial — só persiste fatos homologados para contexto analítico."""
    if not parsed.get("incorporate"):
        return False
    insight_type = str(parsed.get("insight_type") or "general").lower()
    if insight_type in ("proposal", "question", "general"):
        return False
    return insight_type == "fact"


def process_user_message(db: Session, message: str, user_id: int | None = None) -> dict:
    text = (message or "").strip()
    if not text:
        return {
            "response": "Envie uma pergunta ou proposta sobre um jogo específico.",
            "is_valid": False,
            "verdict": "n/a",
        }

    context = build_chat_context(db, text, user_id)
    teams = teams_mentioned_in_message(db, text)
    fixtures = find_relevant_fixtures(db, teams)
    user_content = (
        f"Mensagem do usuário:\n{text}\n\n"
        f"Pacote de contexto (JSON):\n{json.dumps(context, ensure_ascii=False, indent=2)}"
    )

    try:
        raw = chat_completion(
            COLLECTIVE_SYSTEM_PROMPT,
            user_content,
            temperature=0.35,
            max_tokens=2200,
            feature="chat_collective",
        )
        parsed = _parse_json_from_llm(raw)
        if not parsed:
            return {
                "response": (
                    "Não consegui montar uma resposta estruturada. "
                    "Tente citar os times (ex.: Colômbia x RD Congo) e o mercado que você viu."
                ),
                "is_valid": False,
                "verdict": "n/a",
            }

        response_text = str(parsed.get("response") or "").strip()
        if not response_text:
            response_text = "Recebi sua mensagem, mas não consegui formular a resposta."

        team_id = parsed.get("identified_team_id")
        fixture_id = parsed.get("identified_fixture_id")
        if fixture_id and not db.get(Fixture, int(fixture_id)):
            fixture_id = None
        if team_id and not db.get(Team, int(team_id)):
            team_id = None
        if not team_id and teams:
            team_id = teams[0].id
        if not fixture_id and fixtures:
            fixture_id = fixtures[0].id

        insight_type = str(parsed.get("insight_type") or "general")[:20]
        incorporate = _resolve_incorporate(parsed)
        # Palpite oficial (FixtureReport / pipeline) nunca é escrito aqui — só UserInsight.
        insight = UserInsight(
            user_id=user_id,
            content=text,
            ai_response=response_text,
            evaluation=parsed.get("incorporate_reason") if incorporate else None,
            is_valid=incorporate,
            team_id=int(team_id) if team_id else None,
            fixture_id=int(fixture_id) if fixture_id else None,
            insight_type=insight_type,
            verdict=str(parsed.get("verdict") or "n/a")[:20],
        )
        db.add(insight)
        db.commit()

        return {
            "response": response_text,
            "is_valid": incorporate,
            "evaluation": parsed.get("incorporate_reason"),
            "verdict": parsed.get("verdict", "n/a"),
            "insight_type": parsed.get("insight_type"),
        }
    except Exception as exc:
        return {
            "response": f"Erro ao processar: {exc}",
            "is_valid": False,
            "verdict": "n/a",
        }


def get_valid_insights_for_team(db: Session, team_id: int) -> list[str]:
    """Fatos incorporados no chat — contexto auxiliar na análise; não alteram palpite oficial."""
    insights = (
        db.query(UserInsight)
        .filter(UserInsight.team_id == team_id, UserInsight.is_valid.is_(True))
        .order_by(UserInsight.created_at.desc())
        .limit(5)
        .all()
    )
    rows: list[str] = []
    for item in insights:
        note = item.evaluation or item.ai_response or item.content
        rows.append(f"[Coletiva] {item.content[:180]} — {note[:120]}")
    return rows
