"""Leitura dos skills Cursor (.cursor/skills) para o painel admin root."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SKILLS_ROOT = Path(__file__).resolve().parents[3] / ".cursor" / "skills"

# Resumo em linguagem natural quando o arquivo não tem frontmatter YAML.
PLAIN_PURPOSE: dict[str, str] = {
    "betfair/SKILL.md": (
        "Guia principal do agente: o que é o Palpitaria FC, filosofia de gols, "
        "pipeline e regras que devo seguir em toda conversa neste projeto."
    ),
    "betfair/context.md": (
        "Memória viva do projeto — decisões tomadas, estado atual, MVP Copa 2026 "
        "e o que ainda está em aberto. Atualizo quando algo importante muda."
    ),
    "betfair/competition-data-strategies.md": (
        "Como buscar e combinar dados por tipo de competição (liga, copa, seleções) "
        "sem depender de uma fonte só."
    ),
    "betfair/copa-mundo-stalking.md": (
        "Mapa de fontes web para a Copa — quais sites priorizar no stalking "
        "e como montar buscas por bastidores e placares."
    ),
    "competitions/WC.md": (
        "Especialista Copa do Mundo: amostras curtas, perfil híbrido API+web, "
        "volatilidade e regras específicas de seleções."
    ),
    "competitions/BSA.md": (
        "Especialista Brasileirão Série A: pontos corridos, mando de campo, "
        "API first e motor Poisson."
    ),
    "competitions/BSB.md": (
        "Especialista Brasileirão Série B: mando forte, variância alta, "
        "edge mais exigente e Over 1.5 como core."
    ),
    "competitions/CDB.md": (
        "Especialista Copa do Brasil: mata-mata, motivação e rotação de elenco."
    ),
}


@dataclass
class SkillDocSummary:
    rel_path: str
    title: str
    purpose: str
    description: str | None
    updated_at: datetime
    size_bytes: int


@dataclass
class SkillDocDetail(SkillDocSummary):
    body_markdown: str
    body_html: str


def _resolve_skill_path(rel_path: str) -> Path | None:
    if not rel_path or ".." in rel_path.replace("\\", "/"):
        return None
    root = SKILLS_ROOT.resolve()
    target = (root / rel_path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        return None
    if target.suffix.lower() != ".md":
        return None
    return target


def list_skill_docs() -> list[SkillDocSummary]:
    if not SKILLS_ROOT.is_dir():
        return []
    rows: list[SkillDocSummary] = []
    for path in sorted(SKILLS_ROOT.rglob("*.md")):
        rel = path.relative_to(SKILLS_ROOT).as_posix()
        rows.append(_summarize_file(path, rel))
    return rows


def read_skill_doc(rel_path: str) -> SkillDocDetail | None:
    path = _resolve_skill_path(rel_path)
    if path is None:
        return None
    rel = path.relative_to(SKILLS_ROOT).as_posix()
    summary = _summarize_file(path, rel)
    body = path.read_text(encoding="utf-8")
    return SkillDocDetail(
        **summary.__dict__,
        body_markdown=body,
        body_html=render_skill_markdown(body),
    )


def _summarize_file(path: Path, rel_path: str) -> SkillDocSummary:
    text = path.read_text(encoding="utf-8")
    title, description, body = _parse_frontmatter(text)
    if not title:
        title = _title_from_body(body) or rel_path
    purpose = PLAIN_PURPOSE.get(rel_path) or _first_paragraph(body) or description or ""
    stat = path.stat()
    return SkillDocSummary(
        rel_path=rel_path,
        title=title,
        purpose=purpose,
        description=description,
        updated_at=datetime.fromtimestamp(stat.st_mtime),
        size_bytes=stat.st_size,
    )


def _parse_frontmatter(text: str) -> tuple[str | None, str | None, str]:
    if not text.startswith("---"):
        return None, None, text
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not match:
        return None, None, text
    block = match.group(1)
    body = text[match.end() :]
    title = None
    description = None
    name_m = re.search(r"^name:\s*(.+)$", block, re.MULTILINE)
    if name_m:
        title = name_m.group(1).strip().strip('"').strip("'")
    desc_m = re.search(r"^description:\s*>?-\s*\n((?:\s+.+\n?)+)", block, re.MULTILINE)
    if desc_m:
        description = " ".join(line.strip() for line in desc_m.group(1).splitlines()).strip()
    else:
        desc_one = re.search(r'^description:\s*["\']?(.+?)["\']?\s*$', block, re.MULTILINE)
        if desc_one:
            description = desc_one.group(1).strip()
    return title, description, body


def _title_from_body(body: str) -> str | None:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None


def _first_paragraph(body: str) -> str:
    lines: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            if lines:
                break
            continue
        if stripped.startswith("#"):
            continue
        lines.append(stripped)
    return " ".join(lines)[:400]


def render_skill_markdown(text: str) -> str:
    """Markdown leve para leitura no admin (sem dependência extra)."""
    _, _, body = _parse_frontmatter(text)
    out: list[str] = []
    in_ul = False
    in_code = False
    code_lines: list[str] = []

    def close_ul() -> None:
        nonlocal in_ul
        if in_ul:
            out.append("</ul>")
            in_ul = False

    for line in body.splitlines():
        if line.strip().startswith("```"):
            if in_code:
                out.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
                code_lines = []
                in_code = False
            else:
                close_ul()
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue

        if line.startswith("### "):
            close_ul()
            out.append(f"<h3>{_inline_md(line[4:])}</h3>")
        elif line.startswith("## "):
            close_ul()
            out.append(f"<h2>{_inline_md(line[3:])}</h2>")
        elif line.startswith("# "):
            close_ul()
            out.append(f"<h1>{_inline_md(line[2:])}</h1>")
        elif re.match(r"^[-*]\s+", line):
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{_inline_md(re.sub(r'^[-*]\s+', '', line))}</li>")
        elif line.strip() == "":
            close_ul()
        elif line.startswith("|"):
            close_ul()
            out.append(f"<p class='skill-table-line'>{_inline_md(line)}</p>")
        else:
            close_ul()
            out.append(f"<p>{_inline_md(line)}</p>")

    close_ul()
    if in_code and code_lines:
        out.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
    return "\n".join(out)


def _inline_md(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', escaped)
    return escaped
