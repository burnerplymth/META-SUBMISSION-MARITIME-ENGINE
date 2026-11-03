"""Code-side verifier for research notes written by Meta AI.

The model never computes statistics and never writes a verified sentence.
This module is a pure function of (the model's proposed note, the session's
evidence entries, the user's question): no model call, no clock, no I/O.

VERIFIED is closed: a finding carries it only if it is a structured claim
(template id, entity, direction, unit, evidence id, slot values) that
``research_claims`` binds to the evidence (entity present, each value equal to
the evidence value at its own path, direction equal to the sign of the cited
change, unit equal to the series unit) and whose sentence the CODE rendered
from the template. The model's own wording of such a claim is discarded.
A lexical check on free text cannot close this class of false sentence,
so free text does not count.

Everything else the model writes (a finding with only ``text``, a ``context``
line, a limitation) goes through the lexical screen below and is shown as
"Context (unchecked)" or removed, never as verified:
  * it must cite evidence that exists in this session (otherwise it is dropped
    and counted ``dropped_no_evidence``); structured ``numbers`` must match;
  * every number and date in it must occur in the cited evidence, the
    arguments or the question, with role and sign checks (``sign_mismatch``,
    ``role_mismatch``); a contradiction removes it;
  * wording the code cannot check is withheld (flagged, text replaced by a
    fixed placeholder): causal, forecast, hedge, accuracy, multiplicative or
    approximate wording and direction words that no signed figure confirms;
  * war and political vocabulary removes it (``content_policy``), also from
    the title, limitations and any rendered sentence.
"""
from __future__ import annotations

import math
import re
from datetime import date
from decimal import Decimal

from .research_evidence import canonical, numeric_leaves

try:
    from reports.vocab import plain
    from llm.content_policy import POLITICAL_RE
except ImportError:  # imported as server.agent.* from the repo root
    from server.reports.vocab import plain  # type: ignore
    from server.llm.content_policy import POLITICAL_RE  # type: ignore

MAX_FINDINGS_CONSIDERED = 12
MAX_FINDINGS_KEPT = 8
MAX_TEXT = 600
MAX_LIMITATIONS = 6
FLAGGED_TEXT = ("Statement withheld: the wording check removed causal, predictive, accuracy, quantity "
                "or direction phrasing that the code cannot check.")
DEFAULT_TITLE = "Research note"

_NEGATED_OK = re.compile(
    r"\b(?:not|no|never|without)\s+(?:a\s+|an\s+|any\s+)?(?:forecasts?|predictions?|predictive|accuracy|accurate)\b|"
    r"\b(?:does|do|did)\s+not\s+(?:predict|forecast|establish|show|imply|indicate)\s+(?:a\s+|any\s+)?(?:cause|causes|causation|causal)?\b|"
    r"\bnot\s+(?:a\s+)?caus(?:e|es|al|ation)\b|\bnot\s+causes\b|\bno\s+caus(?:e|al|ation)\b|"
    r"\bassociations?,?\s+not\s+causes\b|\bnot\s+evidence\s+of\s+caus(?:e|ation)\b|"
    r"\bcorrelation\s+(?:is\s+)?not\s+causation\b|\bnot\s+(?:a\s+)?cause\b|"
    r"\b(?:cannot|can\s?not|can't|unable\s+to)\s+(?:show|establish|identify|support|demonstrate|imply|indicate|separate)"
    r"\s+(?:a\s+|any\s+)?(?:causes?|causal\w*|causation)\b", re.I)

CAUSAL_RE = re.compile(
    r"\b(?:caus(?:e|es|ed|ing|al|ally|ation)|due\s+to|because|owing\s+to|thanks\s+to|on\s+account\s+of|"
    r"led\s+to|leads?\s+to|leading\s+to|result(?:s|ed|ing)?\s+(?:in|from)|as\s+a\s+(?:result|consequence)|"
    r"contribut(?:e|es|ed|ing|ion)\s+to|trigger(?:s|ed|ing)?|driven\s+by|drove|drives?|driving|"
    r"responsible\s+for|attributable|attributed\s+to|consequence\s+of|effect\s+of|impact\s+of|"
    r"brought\s+about|stemm(?:ed|ing)\s+from|stems?\s+from|therefore|hence|thus|consequently|"
    r"explain(?:s|ed|ing)|affect(?:s|ing)|affected\s+(?:by|the)|influenc(?:e|es|ed|ing)|impact(?:s|ed|ing)|"
    r"induc(?:e|es|ed|ing)|prompt(?:s|ed|ing)|spark(?:s|ed|ing)|gives?\s+rise|reason\s+(?:for|why)|"
    r"in\s+the\s+wake\s+of|in\s+response\s+to|reflect(?:s|ed|ing)|linked\s+to|down\s+to|since\s+the|"
    r"result\s+of|so\s+the|worked)\b", re.I)

FORECAST_RE = re.compile(
    r"\b(?:forecast\w*|predict\w*|projected|projections?|project(?:s|ing)\s+(?:to|that)|prognos\w*|outlook|"
    r"anticipat\w*|expect(?:s|ed|ing|ation|ations)?|likely\s+to|unlikely|going\s+to|will|shall|"
    r"(?:would|could|might|may(?!\s+\d))\s+(?:well\s+)?(?:rise|fall|increase|decrease|recover|continue|persist|"
    r"reach|drop|grow|improve|worsen|rebound|climb|decline|stay|return|ease|extend|spread)|"
    r"probab\w*|chances?|odds|poised|bound\s+to|set\s+to|on\s+track\s+to|upcoming|forthcoming|"
    r"in\s+the\s+coming|going\s+forward|in\s+the\s+future|"
    r"accura\w+|precision|hit[-\s]?rate|beats?\s+(?:the\s+)?baseline|statistically\s+significant|"
    r"p[-\s]?values?|proves?|proven|confirms?\s+that|"
    r"trending|on\s+course|heading\s+(?:lower|higher|down|up)|keeps?\s+(?:rising|falling)|"
    r"robust\w*|reliab\w+|calibrat\w+|validat\w+|trustworth\w+|confiden\w+|correct(?:ly|ness)?|"
    r"significan\w+|definitive\w*|new\s+normal)\b", re.I)

# hedges: allowed in a limitation ("may be incomplete"), not in a finding or a context line,
# where "may have fallen" states a change the code cannot check
HEDGE_RE = re.compile(
    r"\b(?:should|might|could|tends?\s+to|appears?\s+to|seems?\s+to|possibl\w+|perhaps|probabl\w+|"
    r"presumabl\w+)\b|(?-i:\bmay\b(?!\s*\d))", re.I)

# multiplicative, fractional and vague-quantity words: a figure the code cannot check
QUANTITY_RE = re.compile(
    r"\b(?:twice|thrice|double[sd]?|doubling|triple[sd]?|tripling|quadrupl\w+|halv\w+|half|halves|"
    r"(?:a|an|one|two|three|four|five|1|2|3|4|5)[-\s]+(?:third|thirds|quarter|quarters|fifth|fifths|sixth|"
    r"sixths|tenth|tenths|eighth|eighths)|(?:two|three|four|five|six|seven|eight|nine|ten|\d+)[-\s]?fold|"
    r"\d+(?:\.\d+)?\s+times\s+(?:as|higher|lower|more|less|greater|larger|smaller|faster|slower|the)|"
    r"times\s+(?:as\s+(?:much|many|high|low)|higher|lower)|dozens?|hundreds|thousands|millions|"
    r"several|handful|couple|majority|minority)\b|\d\s*[x×]\s*(?:higher|lower|more|less)\b", re.I)

# approximation or bound markers directly in front of a figure
APPROX_RE = re.compile(
    r"(?:\b(?:about|around|roughly|approximately|approx|nearly|almost|circa|close\s+to|more\s+than|"
    r"less\s+than|fewer\s+than|greater\s+than|at\s+least|at\s+most|up\s+to|just\s+(?:over|under))|~)"
    r"\s*(?:[-−+]?\d)", re.I)

_EVID_REF = re.compile(r"[\[(]?\bE\d{1,3}\b[\])]?")
_IDENT_STRIP = re.compile(
    r"\bSAM\s*3(?:\.1)?\b|\bSentinel[-\s]?[12][A-Za-z]*\b|\bS[12][A-Z]?\b|\bL[12][AC]\b|"
    r"\bmuse[-\s]spark[-\s]?[\d.]*\b|\bISO\s*8601\b|\bUTC\b|\bCUSUM\b|\bPELT\b", re.I)
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_DATE_ISO = re.compile(r"(?<![\w-])(\d{4})-(\d{2})(?:-(\d{2}))?(?![\w-])")
_DATE_DMY = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s+(\d{4})\b", re.I)
_DATE_MDY = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", re.I)
_DATE_MY = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{4})\b", re.I)
_YEAR = re.compile(r"(?<![\w.,-])((?:19|20)\d{2})(?![\w]|[.,]\d)")
_NUM_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_.,])(?P<neg>[-−–+]?)(?P<int>\d{1,3}(?:,\d{3})+|\d+)(?P<frac>\.\d+)?"
    r"(?P<suf>\s?%|\s?percent\b|\s?(?:thousand|million|billion)\b|k\b)?", re.I)
_SCALE = {"thousand": 1e3, "k": 1e3, "million": 1e6, "billion": 1e9}


# ---------------------------------------------------------------- helpers
def has_causal_wording(text: str) -> bool:
    t = _NEGATED_OK.sub(" ", text)
    return bool(CAUSAL_RE.search(t))


def has_forecast_wording(text: str, hedges: bool = True) -> bool:
    t = _NEGATED_OK.sub(" ", text)
    return bool(FORECAST_RE.search(t) or (hedges and HEDGE_RE.search(t)))


def has_political_wording(text: str) -> bool:
    return bool(POLITICAL_RE.search(text))


def _norm_text(s: str) -> str:
    return " ".join(plain(str(s)).split())


def _decimals(frac: str | None) -> int:
    return len(frac) - 1 if frac else 0


class _Tok:
    __slots__ = ("raw", "value", "decimals", "explicit_sign", "pct", "scale", "start", "end")

    def __init__(self, raw, value, decimals, explicit_sign, pct, scale, start=0, end=0):
        self.raw, self.value, self.decimals = raw, value, decimals
        self.explicit_sign, self.pct, self.scale = explicit_sign, pct, scale
        self.start, self.end = start, end


def _tokens(text: str) -> list[_Tok]:
    out = []
    for m in _NUM_TOKEN.finditer(text):
        # a minus glued to a word ("COVID-19", "mid-2021") is not a sign
        if m.group("neg") in ("-", "−", "–"):
            before = text[m.start() - 1] if m.start() > 0 else " "
            if before.isalnum():
                continue
        suf = (m.group("suf") or "").strip().lower()
        scale = _SCALE.get(suf, 1.0)
        pct = suf in ("%", "percent")
        base = float((m.group("int") or "0").replace(",", "") + (m.group("frac") or ""))
        neg = m.group("neg")
        sign = -1.0 if neg in ("-", "−", "–") else 1.0
        out.append(_Tok(m.group(0).strip(), sign * base * scale, _decimals(m.group("frac")),
                        bool(neg), pct, scale, m.start(), m.end()))
    return out


def _tok_matched(tok: _Tok, items) -> list[tuple[float, str]]:
    """The (value, path) pool items the written number stands for."""
    tol = 0.5 * (10 ** -tok.decimals) * tok.scale + 1e-9
    v = tok.value
    out = []
    for x, path in items:
        cands = [x]
        if tok.pct:
            cands.append(x * 100.0)
        for c in cands:
            if (abs(c - v) <= tol) if tok.explicit_sign else (abs(abs(c) - abs(v)) <= tol):
                out.append((c, path))
                break
    return out


def _tok_matches(tok: _Tok, pool) -> bool:
    return bool(_tok_matched(tok, [(x, "") for x in pool]))


def value_matches(value, pool, unit: str | None = None) -> bool:
    """A structured numbers[] value against the evidence pool. The value
    matches to the precision it is written with."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return False
    exp = Decimal(repr(value)).as_tuple().exponent
    dec = max(0, -exp) if isinstance(exp, int) else 0
    tol = 0.5 * (10 ** -dec) + 1e-12
    pct = (unit or "").strip().lower() in ("%", "percent", "pct")
    for x in pool:
        for c in ((x, x * 100.0) if pct else (x,)):
            if abs(c - value) <= tol:
                return True
    return False


def _date_candidates(text: str) -> tuple[str, list[str]]:
    """(text with dates removed, [date strings as ISO or year/month stubs])."""
    found: list[str] = []

    def take(iso: str, m) -> str:
        found.append(iso)
        return " "

    def dmy(m):
        mon = _MONTHS[m.group(2).lower()[:3]]
        return take(f"{m.group(3)}-{mon:02d}-{int(m.group(1)):02d}", m)

    def mdy(m):
        mon = _MONTHS[m.group(1).lower()[:3]]
        return take(f"{m.group(3)}-{mon:02d}-{int(m.group(2)):02d}", m)

    def my(m):
        mon = _MONTHS[m.group(1).lower()[:3]]
        return take(f"{m.group(2)}-{mon:02d}", m)

    def iso(m):
        return take("-".join(g for g in m.groups() if g), m)

    t = _DATE_ISO.sub(iso, text)
    t = _DATE_DMY.sub(dmy, t)
    t = _DATE_MDY.sub(mdy, t)
    t = _DATE_MY.sub(my, t)
    t = _YEAR.sub(lambda m: take(m.group(1), m), t)
    return t, found


def _valid_date(iso: str) -> bool:
    parts = iso.split("-")
    try:
        if len(parts) == 3:
            date(int(parts[0]), int(parts[1]), int(parts[2]))
        elif len(parts) == 2:
            date(int(parts[0]), int(parts[1]), 1)
        return True
    except ValueError:
        return False


_SIGNED_LEAF = re.compile(r"(?:delta|diff|difference|change|shift|slope|trend)", re.I)
_ROLE_BEFORE = re.compile(r"(?:^|[._\[])(?:before|pre|prior|baseline)(?:$|[._\]])", re.I)
_ROLE_AFTER = re.compile(r"(?:^|[._\[])(?:after|post|follow\w*)(?:$|[._\]])", re.I)


def _is_signed_leaf(path: str) -> bool:
    leaf = re.split(r"[.\[]", path)[-1] if path else ""
    return bool(_SIGNED_LEAF.search(leaf)) and "abs" not in leaf.lower()


def _sign_of(value: float, path: str) -> int:
    """-1 / +1 for a signed change, 0 for a level or anything unsigned."""
    if value < 0:
        return -1
    if value > 0 and _is_signed_leaf(path):
        return 1
    return 0


def _path_role(path: str) -> int:
    """-1 before, +1 after, 0 neither."""
    if _ROLE_BEFORE.search(path):
        return -1
    if _ROLE_AFTER.search(path):
        return 1
    return 0


class Corpus:
    """What a finding may draw on: the numbers and strings of its cited
    evidence, their arguments, and the user's question."""

    def __init__(self, entries: list[dict], question: str):
        self.items: list[tuple[float, str]] = []   # (value, dotted path)
        self.signed: list[int] = []                # signs of the changes the evidence holds
        parts = [question or ""]
        for e in entries:
            nums = {k: float(v) for k, v in (e.get("numbers") or {}).items()
                    if isinstance(v, (int, float)) and not isinstance(v, bool)}
            for k, v in nums.items():
                self.items.append((v, k))
                sg = _sign_of(v, k)
                if sg:
                    self.signed.append(sg)
            # a change implied by a before/after pair of the same figure
            for k, v in nums.items():
                if _path_role(k) == -1:
                    other = re.sub(r"(?<![A-Za-z])(?:before|pre|prior|baseline)(?![A-Za-z])", "after", k,
                                   count=1, flags=re.I)
                    if other in nums and other != k and k.rsplit(".", 1)[-1] in ("mean", "median", "value", "total"):
                        d = nums[other] - v
                        if d:
                            self.signed.append(1 if d > 0 else -1)
            if isinstance(e.get("n"), (int, float)) and not isinstance(e.get("n"), bool):
                self.items.append((float(e["n"]), "n"))
            self.items += [(float(v), "args." + k) for k, v in numeric_leaves(e.get("args") or {}).items()]
            parts += list((e.get("strings") or {}).values())
            for sv in (e.get("strings") or {}).values():     # a figure quoted from an event title
                if isinstance(sv, str) and " " in sv and any(ch.isdigit() for ch in sv):
                    rest, _ = _date_candidates(sv)
                    self.items += [(abs(tk.value), "title") for tk in _tokens(rest)]
            for path in (e.get("numbers") or {}):      # 90 in days_below_90pct, 95 in p95
                self.items += [(float(x), "pathdigit") for x in re.findall(
                    r"(?<![0-9.])\d+(?![0-9])", re.sub(r"\[\d+\]", "", path))]
            parts.append(canonical(e.get("args") or {}))
            parts.append(str(e.get("analysis_id") or ""))
        self.pool: list[float] = [x for x, _ in self.items]
        self.text = " ".join(parts).lower()
        self.strings = [s for e in entries for s in (e.get("strings") or {}).values()
                        if isinstance(s, str) and len(s) >= 3 and any(ch.isdigit() for ch in s)]

    def has_date(self, iso: str) -> bool:
        return iso.lower() in self.text


# ---- spelled-out numbers: read as numbers, then held to the same standard
_WORD_VAL = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_WORD_SCALE = {"hundred": 100, "thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000}
_NW = "|".join(sorted(list(_WORD_VAL) + list(_WORD_SCALE) + ["dozen"], key=len, reverse=True))
_NUMWORDS_RE = re.compile(rf"\b(?:{_NW})(?:(?:[-\s]+(?:and[-\s]+)?)(?:{_NW}))*\b", re.I)


def _words_value(seq: str) -> float:
    total, cur = 0.0, 0.0
    for w in re.findall(r"[a-z]+", seq.lower()):
        if w in _WORD_VAL:
            cur += _WORD_VAL[w]
        elif w == "dozen":
            cur = (cur or 1) * 12
        elif w == "hundred":
            cur = (cur or 1) * 100
        elif w in _WORD_SCALE:
            total += (cur or 1) * _WORD_SCALE[w]
            cur = 0.0
    return total + cur


def spell_to_digits(text: str) -> str:
    """'twenty to fifteen percent' -> '20 to 15 percent'. A lone 'one' is left
    alone (\"one of the places\" is not a figure)."""
    def rep(m):
        seq = m.group(0)
        if seq.strip().lower() == "one":
            return seq
        v = _words_value(seq)
        return " " + (str(int(v)) if float(v).is_integer() else str(v)) + " "
    return _NUMWORDS_RE.sub(rep, re.sub(r"per[-\s]cent\b", "percent", text, flags=re.I))


# ---- direction and role words
_UP = re.compile(r"^(?:ris(?:e|es|en|ing)|rose|increas\w*|grew|grow(?:s|n|ing|th)?|gain(?:s|ed|ing)?|climb\w*|"
                 r"jump\w*|surg\w*|higher|greater|larger|rebound\w*|recover\w*|improv\w*|expand\w*|"
                 r"upward|uptick|up|uplift\w*|exceed\w*|above|worse\w*|lengthen\w*|longer)$", re.I)
_DOWN = re.compile(r"^(?:fall(?:s|en|ing)?|fell|decreas\w*|drop(?:s|ped|ping)?|declin\w*|reduc\w*|lower\w*|"
                   r"dip(?:s|ped|ping)?|shr[ia]n?k\w*|sink\w*|sank|slump\w*|plung\w*|fewer|smaller|"
                   r"contract\w*|deteriorat\w*|downward|loss|losses|lost|below|beneath|shorten\w*|shorter)$", re.I)
_NEGATORS = frozenset({"not", "no", "never", "without", "neither", "nor", "hardly", "barely", "didn", "doesn",
                       "isn", "wasn", "weren", "hasn", "haven", "don", "cannot", "can"})
_BEFORE_W = frozenset({"before", "prior", "pre", "earlier", "preceding"})
_AFTER_W = frozenset({"after", "following", "post", "later", "subsequent", "afterwards"})
_WORD = re.compile(r"[A-Za-z]+")
_CLAUSE_SEP = re.compile(r"[;:()\[\]]|[.!?,](?=\s|$)")
_FROM_RE = re.compile(r"\bfrom\s*$", re.I)
_FROM_TO_RE = re.compile(r"\bfrom\s+[-−+]?[\d.,]+\s*(?:%|percent)?\s*(?:[A-Za-z/]+\s+){0,2}to\s*$", re.I)
MAX_WORD_DISTANCE = 4


def _words_near(t: str, toks: list[_Tok], tk: _Tok, seps: list[tuple[int, int]]):
    """[(distance, side, word, start)] for the words in the token's clause with
    no other figure between the word and the token, nearest first."""
    lo = max([e for s, e in seps if e <= tk.start] or [0])
    hi = min([s for s, e in seps if s >= tk.end] or [len(t)])
    others = [o for o in toks if o is not tk]
    out = []
    left = [m for m in _WORD.finditer(t, lo, tk.start)]
    right = [m for m in _WORD.finditer(t, tk.end, hi)]
    for rank, m in enumerate(reversed(left), 1):
        if any(m.end() <= o.start and o.end <= tk.start for o in others):
            break
        out.append((rank, 1, m.group(0).lower(), m.start()))
    for rank, m in enumerate(right, 1):
        if any(tk.end <= o.start and o.end <= m.start() for o in others):
            break
        out.append((rank, 0, m.group(0).lower(), m.start()))
    return sorted(out)


def _role_of(t: str, toks, tk: _Tok, seps) -> int:
    pre = t[:tk.start]
    if _FROM_RE.search(pre):
        return -1
    if _FROM_TO_RE.search(pre):
        return 1
    best: dict[int, set[int]] = {}
    for dist, _side, word, _pos in _words_near(t, toks, tk, seps):
        if dist > 3:
            break
        r = -1 if word in _BEFORE_W else (1 if word in _AFTER_W else 0)
        if r:
            best.setdefault(dist, set()).add(r)
    if not best:
        return 0
    roles = best[min(best)]
    return roles.pop() if len(roles) == 1 else 0      # equally near both ways: no role claimed


def _direction_words(t: str, seps) -> list[tuple[int, int, int, bool]]:
    """[(start, end, direction +1/-1, negated)] for each direction word."""
    out = []
    for m in _WORD.finditer(t):
        w = m.group(0)
        d = 1 if _UP.match(w) else (-1 if _DOWN.match(w) else 0)
        if not d:
            continue
        lo = max([e for s, e in seps if e <= m.start()] or [0])
        before = [x.group(0).lower() for x in _WORD.finditer(t, lo, m.start())][-3:]
        out.append((m.start(), m.end(), d, any(b in _NEGATORS for b in before)))
    return out


def _bound_figure(t: str, toks, matched, seps, start: int, end: int):
    """The figure a direction word at [start, end) is about: the nearest
    matched figure in its clause with at most 3 words between and no other
    figure in the way (a figure after the word wins a tie), else None."""
    lo = max([e for s_, e in seps if e <= start] or [0])
    hi = min([s_ for s_, e in seps if s_ >= end] or [len(t)])
    best = None
    for tk in toks:
        if id(tk) not in matched:
            continue
        if tk.end <= start and tk.start >= lo:
            if any(o is not tk and tk.end <= o.start and o.end <= start for o in toks):
                continue
            key = (len(_WORD.findall(t[tk.end:start])), 1)
        elif tk.start >= end and tk.end <= hi:
            if any(o is not tk and end <= o.start and o.end <= tk.start for o in toks):
                continue
            key = (len(_WORD.findall(t[end:tk.start])), 0)
        else:
            continue
        if key[0] < MAX_WORD_DISTANCE and (best is None or key < best[0]):
            best = (key, tk)
    return best[1] if best else None


def analyse_text(text: str, corpus: Corpus) -> tuple[list[str], list[str]]:
    """-> (reject codes, flag codes) for the numbers, dates, roles, signs and
    direction words in ``text``. Reject: the text contradicts or goes beyond
    the evidence. Flag: the text may be true but the code cannot check it."""
    rejects: list[str] = []
    flags: list[str] = []
    t = _EVID_REF.sub(" ", text)
    t = _IDENT_STRIP.sub(" ", t)
    for s in sorted(set(corpus.strings), key=len, reverse=True):
        if s in t:
            t = t.replace(s, " ")
    t = spell_to_digits(t)
    t, dates = _date_candidates(t)
    for d in dates:
        if not _valid_date(d) or not corpus.has_date(d):
            rejects.append("date_not_in_evidence")
            break
    toks = _tokens(t)
    seps = [(m.start(), m.end()) for m in _CLAUSE_SEP.finditer(t)]
    matched: dict[int, list[tuple[float, str]]] = {}
    for tk in toks:
        items = _tok_matched(tk, corpus.items)
        if not items:
            if "text_number_not_in_evidence" not in rejects:
                rejects.append("text_number_not_in_evidence")
            continue
        matched[id(tk)] = items
        role = _role_of(t, toks, tk, seps)
        if role:
            roles = [_path_role(path) for _v, path in items]
            if role not in roles and 0 not in roles:
                if "role_mismatch" not in rejects:
                    rejects.append("role_mismatch")
    # direction words: bound to the nearest figure in the clause, else uncheckable
    for start, end, d, negated in _direction_words(t, seps):
        tk = _bound_figure(t, toks, matched, seps, start, end)
        if negated:
            flags.append("direction_unchecked")
            continue
        corroborated = bool(corpus.signed) and all(sg == d for sg in corpus.signed)
        if tk is None:
            if not corroborated:
                flags.append("direction_unchecked")
            continue
        if tk.explicit_sign:
            if (tk.value < 0) == (d > 0):
                rejects.append("sign_mismatch")
            continue
        signs = [_sign_of(v, path) for v, path in matched[id(tk)]]
        if all(sg == -d for sg in signs if sg) and any(signs) and 0 not in signs:
            rejects.append("sign_mismatch")
        elif d in signs:
            continue
        elif not corroborated:
            flags.append("direction_unchecked")
    rejects = list(dict.fromkeys(rejects))
    flags = list(dict.fromkeys(flags))
    return rejects, flags


def check_numbers_in_text(text: str, corpus: Corpus) -> list[str]:
    """Reason codes for numbers or dates in ``text`` the corpus does not hold
    (and, for the title and limitations, for anything it cannot check)."""
    rejects, flags = analyse_text(text, corpus)
    return rejects + flags


def quantity_codes(text: str) -> list[str]:
    """Flag codes for multiplicative, fractional, vague and approximate wording."""
    t = spell_to_digits(_NEGATED_OK.sub(" ", text))
    codes = []
    if QUANTITY_RE.search(text) or QUANTITY_RE.search(t):
        codes.append("derived_quantity_wording")
    if APPROX_RE.search(t):
        codes.append("approximate_wording")
    return codes


# --------------------------------------------------------------- the verifier
def _entries_for(ids: list[str], by_id: dict) -> list[dict]:
    return [by_id[i] for i in ids if i in by_id]


def _clean_str(v, limit: int) -> str | None:
    if not isinstance(v, str):
        return None
    s = _norm_text(v)
    return s[:limit] if s else None


def _text_gate(text: str) -> list[str]:
    codes = []
    if has_political_wording(text):
        codes.append("content_policy")
    return codes


CONTEXT_LABEL = "Context (unchecked)"
CONTEXT_CHIP = "Unchecked wording"
MAX_CONTEXT = 8
_TITLE_BAD = re.compile(r"\d|[%=]|\b(?:rise|rises|rose|risen|fall|falls|fell|fallen|up|down|higher|lower|more|less|"
                        r"increase\w*|decrease\w*|drop\w*|declin\w*|grew|grow\w*|surg\w*|improv\w*|worse\w*)\b", re.I)


def _title_ok(title: str, corpus: Corpus) -> bool:
    return not (has_causal_wording(title) or has_forecast_wording(title) or has_political_wording(title)
                or quantity_codes(title) or check_numbers_in_text(title, corpus) or _TITLE_BAD.search(title)
                or len(title.split()) > 14)


def _screen_free_text(text: str, ev_ids: list[str], by_id: dict, question: str):
    """The lexical screen, used only for free text. -> ("unchecked"|"flagged"|"rejected", codes).
    Nothing that passes is ever counted as verified."""
    codes = _text_gate(text)
    if codes:
        return "rejected", codes
    wording = []
    if has_causal_wording(text):
        wording.append("causal_wording")
    if has_forecast_wording(text):
        wording.append("forecast_wording")
    wording += quantity_codes(text)
    if not wording:
        rejects, flags = analyse_text(text, Corpus(_entries_for(ev_ids, by_id), question))
        if rejects:
            return "rejected", rejects
        wording += flags
    return ("flagged", wording) if wording else ("unchecked", [])


def verify_note(raw, evidence: list[dict], question: str = "") -> tuple[dict, dict]:
    """-> (clean, report).

    A finding is VERIFIED only if it is a structured claim (``template_id``,
    entity, direction, unit, evidence, values) that ``research_claims`` binds
    to the evidence; the sentence is then written by code. Any other text the
    model wrote (a finding with only ``text``, a ``context`` line, a
    limitation) is screened and shown as unchecked context, never verified.

    clean  {"title", "findings": [{"id","text","state":"verified","evidence","numbers",
                                   "template_id","entity","checked":True}],
            "context": [{"id","text","state":"unchecked"|"flagged","kind","evidence","label","chip"}],
            "limitations": [...]}   (limitations: screened model sentences, unchecked)
    report {"proposed","verified","flagged","rejected","dropped_no_evidence","unchecked",
            "limitations_dropped","reasons":[{"id","state","codes"}]}
    """
    from . import research_claims as RC

    by_id = {e["id"]: e for e in evidence}
    all_corpus = Corpus(list(by_id.values()), question)
    report = {"proposed": 0, "verified": 0, "flagged": 0, "rejected": 0,
              "dropped_no_evidence": 0, "unchecked": 0, "limitations_dropped": 0, "reasons": []}
    clean = {"title": DEFAULT_TITLE, "findings": [], "context": [], "limitations": []}
    if not isinstance(raw, dict):
        report["reasons"].append({"id": "-", "state": "rejected", "codes": ["bad_shape"]})
        return clean, report

    # ---- title (model wording: kept only when it is a plain noun phrase)
    title = _clean_str(raw.get("title"), 120)
    if title and _title_ok(title, all_corpus):
        clean["title"] = title

    def add_context(kind: str, text: str, ev_ids: list[str], cid: str):
        if len(clean["context"]) >= MAX_CONTEXT:
            return False
        state, codes = _screen_free_text(text, ev_ids, by_id, question)
        if state == "rejected":
            report["rejected"] += 1
            report["reasons"].append({"id": cid, "state": "rejected", "codes": codes})
            return False
        clean["context"].append({
            "id": f"C{len(clean['context']) + 1}", "kind": kind,
            "text": text if state == "unchecked" else FLAGGED_TEXT, "state": state,
            "evidence": ev_ids, "label": CONTEXT_LABEL, "chip": CONTEXT_CHIP})
        if state == "flagged":
            report["flagged"] += 1
            report["reasons"].append({"id": cid, "state": "flagged", "codes": codes})
        else:
            report["unchecked"] += 1
        return True

    def evidence_ids(f) -> list[str] | None:
        ev = f.get("evidence")
        ev = [ev] if isinstance(ev, str) else ev
        ev = [x for x in ev if isinstance(x, str)] if isinstance(ev, list) else []
        return list(dict.fromkeys(x.strip().upper() for x in ev))

    # ---- findings
    proposed = raw.get("findings")
    proposed = proposed if isinstance(proposed, list) else []
    seen_ids: set[str] = set()
    seen_text: set[str] = set()
    for idx, f in enumerate(proposed[:MAX_FINDINGS_CONSIDERED], 1):
        report["proposed"] += 1
        fid = _clean_str(f.get("id"), 12) if isinstance(f, dict) else None
        fid = fid if fid and re.fullmatch(r"F\d{1,2}", fid) and fid not in seen_ids else f"F{idx}"
        seen_ids.add(fid)
        if not isinstance(f, dict):
            _reject(report, fid, ["bad_shape"])
            continue
        ev_ids = evidence_ids(f)
        if not ev_ids:
            report["dropped_no_evidence"] += 1
            _reject(report, fid, ["no_evidence"])
            continue
        if any(x not in by_id for x in ev_ids):
            _reject(report, fid, ["unknown_evidence"])
            continue
        if f.get("template_id") not in (None, ""):
            res = RC.check_claim(f, by_id, evidence)
            if not res.ok:
                _reject(report, fid, [res.code])
                continue
            if has_political_wording(res.text) or res.text in seen_text:
                _reject(report, fid, ["content_policy" if not res.text in seen_text else "duplicate"])
                continue
            if len(clean["findings"]) >= MAX_FINDINGS_KEPT:
                _reject(report, fid, ["over_cap"])
                continue
            seen_text.add(res.text)
            clean["findings"].append({"id": fid, "text": res.text, "state": "verified", "evidence": res.evidence,
                                      "numbers": res.numbers, "template_id": res.template_id,
                                      "entity": res.entity, "checked": True})
            report["verified"] += 1
            continue
        # free text: never verified
        text = _clean_str(f.get("text"), MAX_TEXT)
        if not text:
            _reject(report, fid, ["empty_text"])
            continue
        # a figure written in `numbers` must still exist in the cited evidence
        raw_nums = f.get("numbers") if isinstance(f.get("numbers"), list) else []
        num_codes = []
        for nrow in raw_nums[:12]:
            if not isinstance(nrow, dict):
                num_codes.append("bad_shape")
                continue
            nev = nrow.get("evidence")
            nev = nev.strip().upper() if isinstance(nev, str) else None
            cites = [nev] if nev else ev_ids
            if any(c not in by_id or c not in ev_ids for c in cites):
                num_codes.append("unknown_evidence")
            elif not value_matches(nrow.get("value"), Corpus(_entries_for(cites, by_id), "").pool,
                                   _clean_str(nrow.get("unit"), 24) or ""):
                num_codes.append("number_not_in_evidence")
        if num_codes:
            _reject(report, fid, sorted(set(num_codes)))
            continue
        add_context("finding", text, ev_ids, fid)

    # ---- context lines (free text next to the claims)
    ctx = raw.get("context")
    for j, c in enumerate((ctx if isinstance(ctx, list) else [])[:MAX_CONTEXT], 1):
        text = _clean_str(c.get("text") if isinstance(c, dict) else c, MAX_TEXT)
        ev_ids = evidence_ids(c) if isinstance(c, dict) else []
        if not text or any(x not in by_id for x in ev_ids):
            report["limitations_dropped"] += 1
            continue
        add_context("context", text, ev_ids, f"C{j}")

    # ---- limitations (model wording, screened, unchecked)
    lims = raw.get("limitations")
    for l in (lims if isinstance(lims, list) else [])[:MAX_LIMITATIONS * 2]:
        s = _clean_str(l, 300)
        if (not s or has_causal_wording(s) or has_forecast_wording(s, hedges=False) or has_political_wording(s)
                or quantity_codes(s) or check_numbers_in_text(s, all_corpus)
                or len(clean["limitations"]) >= MAX_LIMITATIONS):
            report["limitations_dropped"] += 1
            continue
        clean["limitations"].append(s)
    return clean, report


def _reject(report: dict, fid: str, codes: list[str]) -> None:
    report["rejected"] += 1
    report["reasons"].append({"id": fid, "state": "rejected", "codes": codes})
