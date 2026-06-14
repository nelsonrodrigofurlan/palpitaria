"""Brazilian Portuguese display names for national teams (Copa 2026)."""

from __future__ import annotations

import unicodedata

# football-data.org external_id → nome padrão mídia BR
EXTERNAL_ID_TO_PT: dict[int, str] = {
    778: "Argélia",
    762: "Argentina",
    779: "Austrália",
    816: "Áustria",
    805: "Bélgica",
    1060: "Bósnia-Herzegovina",
    764: "Brasil",
    828: "Canadá",
    1930: "Cabo Verde",
    818: "Colômbia",
    1934: "RD Congo",
    799: "Croácia",
    9460: "Curaçao",
    798: "República Tcheca",
    791: "Equador",
    825: "Egito",
    770: "Inglaterra",
    773: "França",
    759: "Alemanha",
    763: "Gana",
    836: "Haiti",
    840: "Irã",
    8062: "Iraque",
    1935: "Costa do Marfim",
    766: "Japão",
    8049: "Jordânia",
    769: "México",
    815: "Marrocos",
    8601: "Holanda",
    783: "Nova Zelândia",
    8872: "Noruega",
    1836: "Panamá",
    761: "Paraguai",
    765: "Portugal",
    8030: "Catar",
    801: "Arábia Saudita",
    8873: "Escócia",
    804: "Senegal",
    774: "África do Sul",
    772: "Coreia do Sul",
    760: "Espanha",
    792: "Suécia",
    788: "Suíça",
    802: "Tunísia",
    803: "Turquia",
    771: "Estados Unidos",
    758: "Uruguai",
    8070: "Uzbequistão",
}

# Variantes API / web em inglês (e grafias alternativas)
ENGLISH_ALIASES: dict[str, str] = {
    "algeria": "Argélia",
    "argentina": "Argentina",
    "australia": "Austrália",
    "austria": "Áustria",
    "belgium": "Bélgica",
    "bosnia-herzegovina": "Bósnia-Herzegovina",
    "bosnia and herzegovina": "Bósnia-Herzegovina",
    "brazil": "Brasil",
    "canada": "Canadá",
    "cape verde islands": "Cabo Verde",
    "cape verde": "Cabo Verde",
    "colombia": "Colômbia",
    "congo dr": "RD Congo",
    "dr congo": "RD Congo",
    "democratic republic of the congo": "RD Congo",
    "croatia": "Croácia",
    "curacao": "Curaçao",
    "curaçao": "Curaçao",
    "czechia": "República Tcheca",
    "czech republic": "República Tcheca",
    "ecuador": "Equador",
    "egypt": "Egito",
    "england": "Inglaterra",
    "france": "França",
    "germany": "Alemanha",
    "ghana": "Gana",
    "haiti": "Haiti",
    "iran": "Irã",
    "iraq": "Iraque",
    "ivory coast": "Costa do Marfim",
    "cote d'ivoire": "Costa do Marfim",
    "japan": "Japão",
    "jordan": "Jordânia",
    "mexico": "México",
    "morocco": "Marrocos",
    "netherlands": "Holanda",
    "new zealand": "Nova Zelândia",
    "norway": "Noruega",
    "panama": "Panamá",
    "paraguay": "Paraguai",
    "portugal": "Portugal",
    "qatar": "Catar",
    "saudi arabia": "Arábia Saudita",
    "scotland": "Escócia",
    "senegal": "Senegal",
    "south africa": "África do Sul",
    "south korea": "Coreia do Sul",
    "korea republic": "Coreia do Sul",
    "spain": "Espanha",
    "sweden": "Suécia",
    "switzerland": "Suíça",
    "tunisia": "Tunísia",
    "turkey": "Turquia",
    "turkiye": "Turquia",
    "united states": "Estados Unidos",
    "usa": "Estados Unidos",
    "uruguay": "Uruguai",
    "uzbekistan": "Uzbequistão",
}

# Inglês canônico (API) por external_id — para buscas web
EXTERNAL_ID_TO_EN: dict[int, str] = {eid: en for en, eid in {
    "Algeria": 778, "Argentina": 762, "Australia": 779, "Austria": 816,
    "Belgium": 805, "Bosnia-Herzegovina": 1060, "Brazil": 764, "Canada": 828,
    "Cape Verde Islands": 1930, "Colombia": 818, "Congo DR": 1934,
    "Croatia": 799, "Curaçao": 9460, "Czechia": 798, "Ecuador": 791,
    "Egypt": 825, "England": 770, "France": 773, "Germany": 759, "Ghana": 763,
    "Haiti": 836, "Iran": 840, "Iraq": 8062, "Ivory Coast": 1935, "Japan": 766,
    "Jordan": 8049, "Mexico": 769, "Morocco": 815, "Netherlands": 8601,
    "New Zealand": 783, "Norway": 8872, "Panama": 1836, "Paraguay": 761,
    "Portugal": 765, "Qatar": 8030, "Saudi Arabia": 801, "Scotland": 8873,
    "Senegal": 804, "South Africa": 774, "South Korea": 772, "Spain": 760,
    "Sweden": 792, "Switzerland": 788, "Tunisia": 802, "Turkey": 803,
    "United States": 771, "Uruguay": 758, "Uzbekistan": 8070,
}.items()}


def _normalize_key(name: str) -> str:
    lowered = name.lower().strip()
    nfkd = unicodedata.normalize("NFKD", lowered)
    asciiish = "".join(c for c in nfkd if not unicodedata.combining(c))
    return asciiish.replace("  ", " ")


def localize_team_name(name: str, external_id: int | None = None) -> str:
    """Return Brazilian Portuguese display name."""
    if external_id and external_id in EXTERNAL_ID_TO_PT:
        return EXTERNAL_ID_TO_PT[external_id]
    key = _normalize_key(name)
    if key in ENGLISH_ALIASES:
        return ENGLISH_ALIASES[key]
    # Already PT or unknown — keep as-is
    for alias_key, pt in ENGLISH_ALIASES.items():
        if pt.lower() == name.lower():
            return pt
    return name


def english_team_name(name: str, external_id: int | None = None) -> str:
    """API / search-friendly English name."""
    if external_id and external_id in EXTERNAL_ID_TO_EN:
        return EXTERNAL_ID_TO_EN[external_id]
    key = _normalize_key(name)
    for en, pt in ENGLISH_ALIASES.items():
        if _normalize_key(pt) == key:
            return en.title() if " " not in en else en
    return name


def names_for_matching(name: str, external_id: int | None = None) -> set[str]:
    """All name variants for matching web/API snippets."""
    pt = localize_team_name(name, external_id)
    en = english_team_name(name, external_id)
    variants = {_normalize_key(pt), _normalize_key(en), _normalize_key(name)}
    if external_id and external_id in EXTERNAL_ID_TO_EN:
        variants.add(_normalize_key(EXTERNAL_ID_TO_EN[external_id]))
    return {v for v in variants if v}
