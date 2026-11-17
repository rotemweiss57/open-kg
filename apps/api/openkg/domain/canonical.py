"""Deterministic, cautious canonicalization. Everything here is pure and unit-tested."""
from __future__ import annotations

import hashlib
import ipaddress
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = {"gclid", "fbclid", "ref", "ref_src", "mc_cid", "mc_eid", "igshid", "si"}
ORG_SUFFIXES = re.compile(r"\b(inc|incorporated|llc|ltd|limited|corp|corporation|co|gmbh|plc|sa|ag|bv)\.?$", re.I)
_DATE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")


_DASHES = re.compile("[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]")
_QUOTES = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'})


def sha(*parts: object) -> str:
    return hashlib.sha256("\x1f".join(str(p) for p in parts).encode()).hexdigest()


def norm_text(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = _DASHES.sub("-", text).translate(_QUOTES)  # "Mercedes‑Benz" (non-breaking hyphen) is "Mercedes-Benz"
    return re.sub(r"\s+", " ", text).strip().lower()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", norm_text(text)).strip("-")


def norm_name(name: str, entity_type: str = "") -> str:
    n = norm_text(name)
    n = re.sub(r"[.,'’\"()]", "", n)
    if entity_type.lower() in {"company", "organization", "investor", "org", "fund"}:
        n = ORG_SUFFIXES.sub("", n).strip()
    return re.sub(r"\s+", " ", n)


def norm_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        f = float(value)
        return str(int(f)) if f.is_integer() and abs(f) < 1e15 else repr(f)  # 2012 and "2012" are one value
    s = norm_text(str(value))
    return re.sub(r"[.,;:!]+$", "", s)[:400]


def domain_of(url: str) -> str:
    try:
        host = urlsplit(url if "://" in url else "https://" + url).hostname or ""
    except ValueError:
        return ""
    return host.lower().removeprefix("www.")


def canonical_url(url: str) -> str:
    url = (url or "").strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        return url[:700]
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower().removeprefix("www.")
    if parts.port and parts.port not in (80, 443):
        host = f"{host}:{parts.port}"
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS]
    path = re.sub(r"/+$", "", parts.path) or ""
    if scheme in ("http", "https"):
        scheme = "https"
    return urlunsplit((scheme, host, path, urlencode(sorted(query)), ""))[:700]


def is_public_http_url(url: str) -> bool:
    """SSRF guard for anything handed to a web tool. Only public http(s) hosts pass."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    if parts.username or parts.password:
        return False
    host = parts.hostname.lower().rstrip(".")
    if host in {"localhost", "metadata.google.internal"} or "." not in host:
        return False
    if host.endswith((".local", ".internal", ".localhost", ".lan", ".home.arpa")):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return not re.fullmatch(r"[0-9.]+|0x[0-9a-f]+", host)  # reject odd numeric encodings
    return ip.is_global


def valid_date(value: str | None) -> str | None:
    if not value:
        return None
    value = str(value).strip()[:10]
    return value if _DATE.match(value) else None


def entity_key(entity_type: str, name: str, identifiers: dict[str, str] | None = None,
               disambiguator: str | None = None) -> str:
    """Deterministic identity key.

    A strong identifier (official domain / profile URL) wins. Otherwise, for a person, the key
    includes a disambiguator so two people who merely share a name do NOT collapse into one node;
    such pairs surface as merge suggestions for a human instead. Organisations, places and things
    are keyed by name alone: "CATL (supplier to BMW)" and "CATL (supplier to VW)" are one company.
    """
    t = slug(entity_type)
    ids = {k: v for k, v in (identifiers or {}).items() if v}
    if ids.get("domain"):
        return f"{t}:domain:{domain_of(ids['domain'])}"[:400]
    if ids.get("profile_url"):
        return f"{t}:url:{canonical_url(ids['profile_url'])}"[:400]
    base = f"{t}:name:{slug(norm_name(name, entity_type))}"
    if disambiguator and t == "person":
        base += f"@{slug(norm_name(disambiguator, 'company'))}"
    return base[:400]


# --- predicate and value equivalence ---------------------------------------------------------------
# Models name the same attribute many ways. Each family collapses to one predicate so that
# "founding_year" corroborates "founded_year" instead of sitting beside it as a second fact.
PREDICATE_FAMILIES: tuple[tuple[str, ...], ...] = (
    ("founded_year", "founding_year", "year_founded", "founded", "founded_in", "founded_date", "founding_date",
     "date_founded", "established", "year_established", "inception_year"),
    ("hq_location", "hq", "headquarters", "headquarters_location", "headquartered_in", "head_office", "hq_city",
     "headquarter_location"),
    ("employee_count", "employees", "number_of_employees", "headcount", "team_size", "num_employees"),
    ("funding_stage", "latest_funding_stage", "latest_round", "funding_round", "latest_funding_round"),
    ("total_funding", "funding_total", "total_raised", "total_funding_usd", "funding_raised", "total_funding_raised"),
    ("title", "job_title", "current_title", "position", "role", "current_role"),
    ("website", "homepage", "website_url", "url"),
    ("valuation", "latest_valuation", "valuation_usd"),
    ("ticker", "stock_ticker", "ticker_symbol", "stock_symbol"),
)
_FAMILY_OF = {name: fam for fam in PREDICATE_FAMILIES for name in fam}
# Broad names an ontology may use for a family. They are valid targets but are never renamed themselves:
# a person's "location" is not a headquarters.
_ONTOLOGY_ONLY = {"hq_location": ("location",)}


_COUNT_OF = re.compile(r"^(?:number|num|no|count|total_number)_of_(.+)$")


def canonical_predicate(predicate: str, ontology_attributes: set[str] | frozenset[str] = frozenset()) -> str:
    """The ontology's own name for an attribute when it has one, else the family's first name."""
    if predicate in ontology_attributes:
        return predicate
    stripped = re.sub(r"^(?:has|is|its|their)_", "", predicate)  # has_title -> title
    if stripped != predicate and (stripped in ontology_attributes or stripped in _FAMILY_OF):
        predicate = stripped
        if predicate in ontology_attributes:
            return predicate
    counted = _COUNT_OF.match(predicate)  # number_of_charge_points -> charge_points_count
    if counted:
        predicate = f"{counted.group(1)}_count"
        if predicate in ontology_attributes:
            return predicate
    family = _FAMILY_OF.get(predicate)
    if not family:
        return predicate
    return next((name for name in (*family, *_ONTOLOGY_ONLY.get(family[0], ())) if name in ontology_attributes), family[0])


_PLACE_ALIASES = {
    "us": "united states", "usa": "united states", "u.s": "united states", "u.s.a": "united states",
    "united states of america": "united states", "america": "united states",
    "uk": "united kingdom", "u.k": "united kingdom", "great britain": "united kingdom", "england": "united kingdom",
    "uae": "united arab emirates", "holland": "netherlands", "nyc": "new york", "new york city": "new york",
    "sf": "san francisco", "washington dc": "washington", "washington d.c": "washington",
    "al": "alabama", "ak": "alaska", "az": "arizona", "ar": "arkansas", "ca": "california", "calif": "california",
    "co": "colorado", "ct": "connecticut", "de": "delaware", "fl": "florida", "ga": "georgia", "hi": "hawaii",
    "id": "idaho", "il": "illinois", "in": "indiana", "ia": "iowa", "ks": "kansas", "ky": "kentucky",
    "la": "louisiana", "me": "maine", "md": "maryland", "ma": "massachusetts", "mass": "massachusetts",
    "mi": "michigan", "mn": "minnesota", "ms": "mississippi", "mo": "missouri", "mt": "montana", "ne": "nebraska",
    "nv": "nevada", "nh": "new hampshire", "nj": "new jersey", "nm": "new mexico", "ny": "new york",
    "nc": "north carolina", "nd": "north dakota", "oh": "ohio", "ok": "oklahoma", "or": "oregon",
    "pa": "pennsylvania", "ri": "rhode island", "sc": "south carolina", "sd": "south dakota", "tn": "tennessee",
    "tx": "texas", "ut": "utah", "vt": "vermont", "va": "virginia", "wa": "washington", "wv": "west virginia",
    "wi": "wisconsin", "wy": "wyoming", "dc": "washington",
}


def value_parts(value_norm: str) -> list[str]:
    """Comma-separated parts of a normalised value, with articles and common place aliases folded
    ("the Netherlands" = "Netherlands", "CA" = "California", "USA" = "United States")."""
    out = []
    for part in re.split(r"[,;/]| - ", value_norm):
        part = re.sub(r"^the\s+", "", part.strip().strip("."))
        if part:
            out.append(_PLACE_ALIASES.get(part, part))
    return out


def is_location_predicate(predicate: str) -> bool:
    return bool(re.search(r"location|headquarter|(^|_)hq(_|$)|city|address|based_in", predicate))


# Free-text attributes. Two wordings of a description are never a disagreement; the fuller one is kept.
DESCRIPTIVE = frozenset({"description", "summary", "overview", "about", "bio", "biography", "tagline", "profile"})


def same_fact(a: str, b: str, predicate: str = "") -> str | None:
    """Whether two normalised values state one fact at different levels of detail.

    Returns "b" if `b` is the more specific wording, "a" if `a` is, None if they differ.
    "series a" never matches "series b": only whole comma-separated parts are compared.
    """
    if a == b:
        return "a"
    if predicate in DESCRIPTIVE:
        return "b" if len(b) > len(a) else "a"
    try:
        if float(a) == float(b):
            return "a"
    except ValueError:
        pass
    if _DATE.match(a) and _DATE.match(b):  # 2012 and 2012-02-09 are one founding date
        return "b" if b.startswith(a) else "a" if a.startswith(b) else None
    if is_location_predicate(predicate):  # "Paris, France and San Francisco, CA" lists two places
        a, b = (re.sub(r"\s+(?:and|&)\s+", ", ", v) for v in (a, b))
    pa, pb = value_parts(a), value_parts(b)
    if not pa or not pb:
        return None
    sa, sb = set(pa), set(pb)
    if sa == sb:
        return "a"
    if sa < sb:
        return "b"
    if sb < sa:
        return "a"
    if is_location_predicate(predicate) and pa[0] == pb[0]:
        # same city, described by state in one source and by country in another
        return "b" if len(pb) > len(pa) else "a"
    return None


# --- titles --------------------------------------------------------------------------------------
_TITLE_ABBR = {
    "ceo": "chief executive officer", "cto": "chief technology officer", "cfo": "chief financial officer",
    "coo": "chief operating officer", "cmo": "chief marketing officer", "cpo": "chief product officer",
    "cro": "chief revenue officer", "ciso": "chief information security officer", "cio": "chief information officer",
    "cso": "chief strategy officer", "chro": "chief human resources officer", "gc": "general counsel",
    "vp": "vice president", "svp": "senior vice president", "evp": "executive vice president",
    "md": "managing director", "gm": "general manager", "cofounder": "founder", "co founder": "founder",
    "co-founder": "founder",
}


def is_title_predicate(predicate: str) -> bool:
    return predicate in _FAMILY_OF.get("title", ())


def title_roles(value_norm: str) -> frozenset[str]:
    """The distinct roles in a title: "CEO & Co-Founder" -> {chief executive officer, co-founder}."""
    roles = set()
    for part in re.split(r"[,;/&|]| and ", value_norm):
        part = re.sub(r"\s+", " ", part.strip(" ."))
        if part:
            words = [_TITLE_ABBR.get(w, w) for w in part.split(" ")]
            roles.add(_TITLE_ABBR.get(" ".join(words), " ".join(words)))
    return frozenset(roles)


def coexist(a: str, b: str, predicate: str) -> bool:
    """Two different values that are both true at once, so neither contradicts the other. Today
    this covers one case: being a founder alongside holding an office (CEO, and also co-founder)."""
    if not is_title_predicate(predicate):
        return False
    ra, rb = title_roles(a), title_roles(b)
    founder_a, founder_b = any("founder" in r for r in ra), any("founder" in r for r in rb)
    return not (ra & rb) and founder_a != founder_b


# --- quotes --------------------------------------------------------------------------------------
_MD_FOOTNOTE = re.compile(r"\[\[?\d+\]?\]\(#[^)\s]*\)|\[\d+\]")
_MD_LINK = re.compile(r"!?\[([^\]]*)\]\((?:[^()]|\([^()]*\))*\)")
_ELLIPSIS = re.compile(r"\[\s*(?:\.{3,}|…)\s*\]|\.{3,}|…")
_TOKEN = re.compile(r"[^\W_]+")


def plain_words(text: str) -> str:
    """Page text reduced to its words: markdown links, footnote markers, table pipes, heading marks
    and punctuation removed, case and accents folded. Padded with spaces for whole-word search."""
    t = _MD_LINK.sub(r" \1 ", _MD_FOOTNOTE.sub(" ", text or ""))
    return " " + " ".join(_TOKEN.findall(norm_text(t))) + " "


def quote_found(page_words: str, excerpt: str) -> bool:
    """Is `excerpt` a word-for-word quote from the page?

    Formatting and punctuation are ignored, and an ellipsis may skip text, but every stretch of the
    quote must appear on the page with the same words in the same order. A paraphrase fails.
    """
    pos, total = 0, 0
    for segment in _ELLIPSIS.split(excerpt or ""):
        words = plain_words(segment)
        if not words.strip():
            continue
        at = page_words.find(words, max(0, pos - 1))
        if at < 0:
            return False
        pos = at + len(words)
        total += len(words.replace(" ", ""))
    return total >= 8


_GENERIC_NAME_WORDS = {"the", "and", "inc", "llc", "ltd", "corp", "company", "group", "holdings", "technologies",
                       "technology", "labs", "systems", "solutions", "partners", "capital", "ventures", "university",
                       "international", "global", "of", "for"}


def mentions(page_words: str, name: str, aliases: list[str] | tuple[str, ...] = (), person: bool = False) -> bool:
    """Does the page name this entity at all? A page that never does cannot support a fact about it.

    Deliberately generous, because its job is to catch a fact filed under the wrong entity, not to
    judge the fact: a person matches on first name or surname ("Denis, our CTO"), anything else on
    any distinctive word of a name or alias, and scraped text with words run together
    ("PlanetScaleDatabase") still counts.
    """
    def has(word: str) -> bool:
        return word in page_words if len(word) >= 5 else f" {word} " in page_words

    squeezed = None
    for candidate in (name, *aliases):
        words = _TOKEN.findall(norm_text(candidate))
        if not words:
            continue
        if f" {' '.join(words)} " in page_words:
            return True
        keep = [w for w in words if len(w) >= 3 and (person or w not in _GENERIC_NAME_WORDS)]
        if any(has(w) for w in keep):
            return True
        if len(words) > 1:  # "Data Dog" vs "datadog"
            squeezed = squeezed if squeezed is not None else page_words.replace(" ", "")
            if len("".join(words)) >= 5 and "".join(words) in squeezed:
                return True
    return False


_NAME_TAIL = _GENERIC_NAME_WORDS | {"management", "fund", "funds", "investments", "advisors", "associates", "co", "plc",
                                    "gmbh", "sa", "ag", "nv", "bv", "lp", "llp", "limited", "incorporated", "corporation"}


def core_name(name_norm: str) -> str:
    """A name without its trailing generic words: "coatue management" -> "coatue". Used only to
    suggest possible duplicates for a person to confirm, never to merge."""
    words = name_norm.split()
    while len(words) > 1 and words[-1] in _NAME_TAIL:
        words.pop()
    core = " ".join(words)
    return core if len(core) >= 4 else name_norm


# --- funding stages ---------------------------------------------------------------------------------
_STAGES = {"pre-seed": 0, "pre seed": 0, "preseed": 0, "angel": 0, "seed": 1, "ipo": 30, "public": 30, "post-ipo": 31}


def stage_rank(value_norm: str) -> int | None:
    """Position of a financing stage in the usual sequence, or None if the value is not one.
    Stages only move forward, so two different stages are a timeline even without dates."""
    v = value_norm.strip()
    if v in _STAGES:
        return _STAGES[v]
    m = re.fullmatch(r"series ([a-k])(?:[- ]?\d)?", v)
    return 2 + ord(m.group(1)) - ord("a") if m else None


def is_stage_predicate(predicate: str) -> bool:
    return predicate in _FAMILY_OF.get("funding_stage", ()) or predicate.endswith(("_stage", "_round"))
