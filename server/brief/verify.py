"""The verifier: machine-checks writer text against the facts table.

A statement passes only when, after its {Fn} placeholders are set aside:

  * every placeholder names a registered fact;
  * every numeral (and number word) equals a registered value within its own
    rounding (a literal "6.7" matches 6.666667; "7" matches it too);
  * no graph place name or place id appears unless a fact registers the place,
    and no Port/Strait/Canal phrase that no fact registers appears;
  * no URL, domain or known publisher appears unless a fact carries it;
  * no causal, accuracy, prediction, forecast, hedge or likelihood wording, no
    war or political vocabulary, no node/edge/graph vocabulary, no first
    person, no hype, no markdown;
  * a unit written after a figure (days, hours, %, percent ...) is the unit of
    a fact that holds that figure; a rise word before a placeholder or figure
    that the facts hold only as a negative value is a sign mismatch; a month,
    quarter or weekday must occur in a fact.

The check is on the text as written (placeholders unexpanded), so a value the
server inserts is never confused with a value the writer typed.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date

from . import compose, fmt
from .facts import fact_numbers

try:
    from llm.content_policy import POLITICAL_RE
except ImportError:  # imported as server.brief.* from the repo root
    from server.llm.content_policy import POLITICAL_RE  # type: ignore

PH = compose.PH_RE
PCTL_RE = re.compile(r"\bp(?:05|10|25|50|75|90|95)\b", re.IGNORECASE)
NUM_RE = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\w])")
ID_RE = re.compile(r"\b(?:sea|air|gnd|chk|rail|road)_[a-z0-9_]+\b")
BARE_F_RE = re.compile(r"\bF\d+\b")

NOT_A_FORECAST = re.compile(r"\bnot an? (?:forecast|prediction)\b", re.IGNORECASE)

CAUSAL_RE = re.compile(
    r"\b(because|caus(?:e|es|ed|ing)|due to|owing to|as a result|results? in|resulted in|"
    r"resulting in|leads? to|led to|leading to|driven by|drives?|driving|triggers?|triggered|"
    r"attributable|attributed to|stems? from|responsible for|thanks to|on account of|"
    r"consequence of|brought about|gives? rise|contribut\w+ to|therefore|hence|thus|consequently|"
    r"explain(?:s|ed|ing)|affect(?:s|ing)|affected (?:by|the)|influenc\w+|reason (?:for|why)|"
    r"follow(?:s|ed|ing) the|reflect(?:s|ed|ing)|linked to|down to|(?:the )?result of|in the wake|"
    r"in response|came after|comes after|since the|stems?)\b", re.IGNORECASE)
ACCURACY_RE = re.compile(
    r"\b(accurate|accurately|accuracy|precise|precisely|precision|reliable|reliably|"
    r"reliability|validated|validation|guarantee[ds]?|certain|certainly|definitely|"
    r"undoubtedly|proven|robust|confident|confidence|trustworth\w+|calibrat\w+|significan\w+|"
    r"correct|correctly)\b", re.IGNORECASE)
PREDICT_RE = re.compile(
    r"\b(predict\w*|forecast\w*|project(?:ed|ion|ions)|anticipat\w*|expect(?:ed|s|ation|ations)|"
    r"will|going to|likely|unlikely|likelihood|probab\w*|chances?|odds|bound to|poised|"
    r"set to|outlook|prognos\w*|foresee\w*|foreseeable|would|could|might|shall|upcoming|forthcoming|"
    r"should|can|tends? to|seems?|appears?|possibl\w+|perhaps|presumabl\w+|trending|on course|"
    r"heading (?:lower|higher|down|up)|keeps? (?:rising|falling))\b|(?-i:\bmay\b)",
    re.IGNORECASE)
VOCAB_RE = re.compile(r"\b(nodes?|edges?|graphs?)\b", re.IGNORECASE)
HYPE_RE = re.compile(
    r"\b(simply|just|easily|magic\w*|powerful|smart|amazing|supercharge\w*|ai-powered|"
    r"seamless\w*|revolutionary|unprecedented|stunning|incredible)\b", re.IGNORECASE)
PERSON_RE = re.compile(r"\b(we|our|ours|us|i|my|let's)\b", re.IGNORECASE)
MARKDOWN_RE = re.compile(r"[*`#_]{1,}|^\s*[-•]\s", re.MULTILINE)
EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐✅]")
URL_RE = re.compile(r"(https?://|www\.|\b[\w-]+\.(?:com|org|net|gov|int|edu|io|eu|uk|ai|info)\b)",
                    re.IGNORECASE)
PLACE_PHRASE_RES = (
    re.compile(r"\b(?:Port|Strait|Gulf|Cape|Bay|Sea|Canal|Port of|Strait of|Gulf of|Cape of|Bay of|Sea of)"
               r"\s+(?:of\s+)?[A-Z][\w'’-]+(?:\s+[A-Z][\w'’-]+)?"),
    re.compile(r"\b[A-Z][\w'’-]+(?:\s+[A-Z][\w'’-]+)?\s+(?:Canal|Strait|Straits|Harbour|Harbor|"
               r"Channel|Passage|Peninsula)\b"),
)

SOURCE_TERMS = (
    "Reuters", "Bloomberg", "Associated Press", "AP News", "BBC", "Financial Times",
    "Wall Street Journal", "WSJ", "New York Times", "CNN", "Lloyd's List", "Lloyds List",
    "Clarksons", "Drewry", "Sea-Intelligence", "Xeneta", "Maersk", "Hapag-Lloyd", "CMA CGM",
    "UNCTAD", "IMF", "PortWatch", "World Bank", "OECD", "WTO", "NOAA", "NWS", "GDACS", "GDELT",
    "OpenSky", "AISStream", "CBP", "EIA", "Comtrade", "Copernicus", "Sentinel", "MarineTraffic",
    "Marine Traffic", "Windward", "Kpler", "Vortexa", "Eurostat", "Argus", "Platts", "Wikipedia",
    "Gard", "Lloyd's", "S&P Global", "Statista", "Journal of Commerce", "JOC", "Lloyds", "Lloyd",
    "Hapag", "Alphaliner", "Linerlytica", "Flexport", "Freightos", "Container xChange", "TradeWinds",
    "Splash247", "Seatrade", "gCaptain", "FreightWaves", "Loadstar", "Al Jazeera", "Guardian",
    "Economist", "UN data", "United Nations", "WHO", "FAO", "Fitch", "Moody's", "Gartner", "McKinsey",
)

# English words that are also port names in the graph; matched only when the
# name appears with its own capitalisation AND is not on this list.
AMBIGUOUS_NAMES = {"mobile", "split", "nice", "reading", "long", "port", "gulf", "bay", "cape",
                   "sea", "ocean", "channel", "strait", "canal", "central", "south", "north",
                   "east", "west", "island", "river", "harbour", "harbor", "capacity", "queue",
                   "chain", "leg", "legs", "place", "places", "model", "modelled", "run"}

# graph names that are also English words: matched only with their own capital letter
CASE_SENSITIVE_NAMES = {"brake", "colon", "cork", "hull", "male", "soma", "sete", "stade", "kure", "tema",
                        "freeport", "charlotte", "augusta", "midia", "toyo", "kanda", "hanko", "mobile",
                        "brunswick", "taman", "georgetown", "hamilton", "medway", "bristol", "santos",
                        "boston", "virginia", "newcastle", "oran", "naha", "poti", "buco", "tuban"}


def _date_words(text: str) -> str:
    """Month, quarter and weekday names of the ISO dates in ``text`` (a date fact licenses them)."""
    out = []
    for y, m, d in re.findall(r"(\d{4})-(\d{2})-(\d{2})", text):
        try:
            dt = date(int(y), int(m), int(d))
        except ValueError:
            continue
        out += [dt.strftime("%B"), dt.strftime("%b"), dt.strftime("%A"), f"q{(dt.month - 1) // 3 + 1}"]
    return " ".join(out).lower()


def fold(text: str) -> str:
    """Accents removed, so "Reykjavik" and "Reykjavík" are one name."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


NUM_WORDS = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
    "ninety": 90, "hundred": 100, "thousand": 1000, "million": 1_000_000, "billion": 1e9,
    "dozen": 12,
}
NUM_WORD_RE = re.compile(r"\b(" + "|".join(NUM_WORDS) + r")\b", re.IGNORECASE)
VAGUE_RE = re.compile(
    r"\b(double[sd]?|doubling|triple[sd]?|tripling|twice|thrice|halv\w+|half|halves|quadruple[sd]?|"
    r"(?:a|an|one|two|three|four|five)[-\s]+(?:third|thirds|quarter|quarters|fifth|fifths|tenth|tenths)|"
    r"\d+(?:\.\d+)?\s+times|"
    r"\w+-?fold|dozens?|hundreds|thousands|millions|several|handful|couple)\b", re.IGNORECASE)
UP_RE = re.compile(r"\b(?:ris(?:e|es|en|ing)|rose|increas\w*|grew|grow(?:s|n|ing)?|gain(?:s|ed)?|climb\w*|higher|"
                   r"greater|larger|jump\w*|up|uplift\w*|extend\w*|lengthen\w*|longer|worse\w*|exceed\w*|"
                   r"above|surg\w*|add(?:s|ed|ing)?)\b", re.IGNORECASE)
DOWN_RE = re.compile(r"\b(?:fall(?:s|ing)?|fell|decreas\w*|drop(?:s|ped)?|declin\w*|reduc\w*|lower\w*|"
                     r"dip(?:s|ped)?|shr[ia]n?k\w*|smaller|fewer|shorten\w*|shorter|eas(?:e|es|ed|ing)|below)\b",
                     re.IGNORECASE)
# a magnitude the model cannot back from a facts table, or a fraction/multiple written as a symbol
UNVERIFIABLE_RE = re.compile(
    r"\b(?:sizeable|sizable|substantial|considerabl\w+|massive|huge|major|significant|on record|"
    r"in history|all-time|best available|(?:highest|lowest|longest|shortest|biggest|largest|smallest)\s+"
    r"(?:in|on|of|ever|among)\b)\b|"
    r"[¼½¾⅓⅔⅕⅛⅜⅝⅞]|\d\s*[x×](?![\w])", re.IGNORECASE)
# a month, quarter or weekday must come from a fact
TIME_WORD_RE = re.compile(
    r"\b(?:January|February|March|April|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|Q[1-4]|Monday|Tuesday|Wednesday|Thursday|Friday|"
    r"Saturday|Sunday)\b|(?:\b(?:in|on|of|since|until|by|from|during)\s+)May\b|\bMay\s+\d")
# a figure's unit: the word or sign written right after it
UNIT_AFTER_RE = re.compile(
    r"^\s*(?P<u>%|percent\b|per\s?cent\b|hours?\b|hrs?\b|days?\b|d\b|weeks?\b|wks?\b|months?\b|years?\b|yrs?\b|"
    r"events?\b|places?\b|shipments?\b|variants?\b|simulations?\b|feeds?\b)", re.IGNORECASE)
UNIT_CLASS = {"%": "pct", "percent": "pct", "per cent": "pct", "percent ": "pct", "d": "day", "day": "day",
              "days": "day", "hour": "hour", "hours": "hour", "hr": "hour", "hrs": "hour", "week": "week",
              "weeks": "week", "wk": "week", "wks": "week", "month": "month", "months": "month",
              "year": "year", "years": "year", "yr": "year", "yrs": "year",
              "event": "events", "events": "events", "place": "places", "places": "places",
              "shipment": "shipments", "shipments": "shipments", "variant": "variants", "variants": "variants",
              "simulation": "simulations", "simulations": "simulations", "feed": "feeds", "feeds": "feeds"}
FACT_UNIT_CLASS = {"%": "pct", "d": "day", "days": "day", "day": "day", "events": "events", "places": "places",
                   "shipments": "shipments", "variants": "variants", "simulations": "simulations", "feeds": "feeds"}
NOUN_UNITS = {"events", "places", "shipments", "variants", "simulations", "feeds"}
BOUND_RE = re.compile(
    r"\b(?:about|around|roughly|approximately|approx|nearly|almost|circa|close to|exceed\w*|under|above|below|"
    r"beyond|at (?:least|most)|up to|more than|less than|fewer than|no more than|"
    r"just (?:over|under)|upwards of)\s+(?:\w+\s+)?(?:\{F\}|[-−]?\d)|\bover\s+[-−]?\d", re.IGNORECASE)
TIME_NOUN_RE = re.compile(r"\b(?:hours?|weeks?|fortnights?|months?|years?)\b", re.IGNORECASE)
ORDINAL_RE = re.compile(r"\b\d+(?:st|nd|rd|th)\b", re.IGNORECASE)
NEARBY_RE = re.compile(r"\b(?:nearby|neighbou?r\w*|surrounding|adjacent|(?:whole|entire)\s+(?:system|network|chain))\b",
                       re.IGNORECASE)
# a source named after "per", "according to" ... must be one a fact carries
SOURCE_CUE_RE = re.compile(
    r"\b(?:per|according to|reported by|cited by|published by|via)\s+(?:the\s+)?([A-Z][\w&.'’-]*(?:\s+[A-Z][\w&.'’-]*){0,3})")

MAX_STATEMENT_WORDS = 70
MAX_BLOCK_WORDS = 110


class Context:
    """What the verifier needs from the facts table."""

    def __init__(self, facts: list[dict], graph_nodes: list[dict] | None) -> None:
        self.by_id = {f["id"]: f for f in facts}
        self.numbers: list[float] = []
        names: set[str] = set()
        src_text: list[str] = []
        for f in facts:
            self.numbers.extend(fact_numbers(f))
            names.update(n for n in f.get("names") or [] if n)
            src_text.append(str(f.get("display") or ""))
            s = f.get("source") or {}
            for k in ("label", "publisher", "title"):
                if s.get(k):
                    src_text.append(str(s[k]))
            if isinstance(f.get("value"), dict):
                for k in ("label", "publisher", "title", "feed"):
                    if isinstance(f["value"].get(k), str):
                        src_text.append(f["value"][k])
        self.neg_only = self._signed(facts)
        self.pos_vals = [float(f["value"]) for f in facts if isinstance(f.get("value"), (int, float))
                         and not isinstance(f.get("value"), bool) and f["value"] > 0]
        self.neg_vals = [abs(float(f["value"])) for f in facts if isinstance(f.get("value"), (int, float))
                         and not isinstance(f.get("value"), bool) and f["value"] < 0]
        self.names = {fold(n).lower() for n in names}
        self.source_text = " ".join(src_text).lower()
        self.fact_text = fold(" ".join(src_text + [str(f.get("value")) for f in facts])).lower()
        self.fact_text += " " + _date_words(self.fact_text)
        # (number, unit class) for every numeral a fact holds: a unit written after a figure
        # must be the unit of a fact that holds the figure
        self.num_units: list[tuple[float, str]] = []
        for f in facts:
            cls = FACT_UNIT_CLASS.get(str(f.get("unit") or "").strip().lower(), "other")
            self.num_units.extend((x, cls) for x in fact_numbers(f))
        self.graph_names: list[str] = []
        for n in graph_nodes or []:
            nm = str(n.get("name") or "").strip()
            if len(nm) >= 4 and nm.lower() not in AMBIGUOUS_NAMES:
                self.graph_names.append(fold(nm))
        # longest first so "Port Said" is read before "Said"
        self.graph_names = sorted(set(self.graph_names), key=lambda x: (-len(x), x))
        self._graph_rx = None

    def graph_rx(self):
        """Place names, matched on accent-folded text: with their own capital letter when the name is also an
        English word, in any case otherwise ("rotterdam", "ROTTERDAM"); a possessive or hyphen suffix
        ("Rotterdam's port", "Hamburg-bound") does not hide the name."""
        if self._graph_rx is None and self.graph_names:
            strict = [n for n in self.graph_names if n.lower() in CASE_SENSITIVE_NAMES]
            loose = [n for n in self.graph_names if n.lower() not in CASE_SENSITIVE_NAMES]
            parts = []
            if loose:
                parts.append("(?i:" + "|".join(re.escape(n) for n in loose) + ")")
            if strict:
                parts.append("|".join(re.escape(n) for n in strict))
            self._graph_rx = re.compile(r"(?<![\w])(" + "|".join(parts) + r")(?![\w])")
        return self._graph_rx


    @staticmethod
    def _signed(facts: list[dict]) -> set[float]:
        """Magnitudes (3 dp) that facts hold only as negative values."""
        neg, pos = set(), set()
        for f in facts:
            v = f.get("value")
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            (neg if v < 0 else pos).add(round(abs(float(v)), 3))
        return neg - pos


def number_allowed(x: float, decimals: int, allowed: list[float]) -> bool:
    tol = 0.5 * (10 ** -decimals) + 1e-9
    for y in allowed:
        if abs(round(y, decimals) - x) <= 1e-9 or abs(y - x) <= tol:
            return True
    return False


def _name_known(name: str, ctx: Context) -> bool:
    low = fold(name).lower()
    return any(low == k or low in k or k in low for k in ctx.names)


def check_statement(text: str, ctx: Context) -> list[str]:
    """Reasons a statement fails; [] when it passes."""
    reasons: list[str] = []
    if not isinstance(text, str) or not text.strip():
        return ["empty"]
    raw = text
    ids = PH.findall(raw)
    for f in ids:
        if f not in ctx.by_id:
            reasons.append(f"unknown_fact:{f}")
    body = PH.sub(" ", raw)
    if "{" in body or "}" in body or BARE_F_RE.search(body):
        reasons.append("malformed_placeholder")
    body = NOT_A_FORECAST.sub(" ", body)
    body = PCTL_RE.sub(" ", body)
    if len(raw.split()) > MAX_STATEMENT_WORDS:
        reasons.append("too_long")
    # a rise word straight before a placeholder whose fact is negative: "rises by {F3}" with F3 = -5.2
    for m in PH.finditer(raw):
        fact = ctx.by_id.get(m.group(1) if m.groups() else m.group(0).strip("{}"))
        v = fact.get("value") if fact else None
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v < 0:
            lead = re.split(r"[;:.,()]", raw[:m.start()])[-1].split()[-4:]
            if UP_RE.search(" ".join(lead)) and not DOWN_RE.search(" ".join(lead)):
                reasons.append("sign_mismatch")
    if not ids and not NUM_RE.search(body):
        reasons.append("no_fact_reference")

    for m in NUM_RE.finditer(body):
        tok = m.group(0)
        try:
            x = float(tok.replace(",", ""))
        except ValueError:
            continue
        dec = len(tok.split(".")[1]) if "." in tok else 0
        if not number_allowed(x, dec, ctx.numbers):
            reasons.append(f"number_not_in_facts:{tok}")
        else:
            um = UNIT_AFTER_RE.match(body[m.end():m.end() + 14])
            if um:
                cls = UNIT_CLASS.get(re.sub(r"\s+", " ", um.group("u").lower()), "other")
                tol = 0.5 * (10 ** -dec) + 1e-9
                held = [c for y, c in ctx.num_units if abs(round(y, dec) - x) <= 1e-9 or abs(y - x) <= tol]
                # a time or percent unit needs a fact in that unit; a counted noun only conflicts with a
                # fact that names a different unit
                if cls not in held and (cls not in NOUN_UNITS or any(c != "other" for c in held)):
                    reasons.append(f"unit_mismatch:{tok}")
        if (number_allowed(x, dec, ctx.pos_vals) and not number_allowed(x, dec, ctx.neg_vals) and m.start() > 0 and body[m.start() - 1] in "-−"
                and (m.start() == 1 or body[m.start() - 2] in " (\t\n")):
            reasons.append("sign_mismatch")
        # a magnitude the facts hold only as a negative value must not be written with a rise word
        if round(x, 3) in ctx.neg_only and not re.search(r"[-−]\s*$", body[:m.start()]):
            near = body[max(0, m.start() - 40):m.start()] + " " + body[m.end():m.end() + 20]
            if UP_RE.search(near) and not DOWN_RE.search(near):
                reasons.append("sign_mismatch")
    for m in NUM_WORD_RE.finditer(body):
        v = NUM_WORDS[m.group(1).lower()]
        if not number_allowed(float(v), 0, ctx.numbers):
            reasons.append(f"number_word_not_in_facts:{m.group(1).lower()}")
    if VAGUE_RE.search(body):
        reasons.append("derived_quantity_wording")

    for tok in ID_RE.findall(body):
        reasons.append(f"place_id_in_text:{tok}")
    rx = ctx.graph_rx()
    if rx is not None:
        for m in rx.finditer(fold(body)):
            if not _name_known(m.group(1), ctx):
                reasons.append(f"place_not_in_facts:{m.group(1)}")
    for prx in PLACE_PHRASE_RES:
        for m in prx.finditer(body):
            phrase = m.group(0)
            if not _name_known(phrase, ctx) and not any(
                    phrase.lower() in n or n in phrase.lower() for n in ctx.names):
                reasons.append(f"place_not_in_facts:{phrase}")
    if URL_RE.search(body):
        reasons.append("url_in_text")
    for term in SOURCE_TERMS:
        if re.search(r"(?<![\w])" + re.escape(term) + r"(?![\w])", body, re.IGNORECASE):
            if term.lower() not in ctx.source_text:
                reasons.append(f"source_not_in_facts:{term}")

    for m in SOURCE_CUE_RE.finditer(body):
        cited = fold(m.group(1)).lower()
        if cited not in ctx.fact_text and not _name_known(cited, ctx):
            reasons.append(f"source_not_in_facts:{m.group(1)}")
    for m in TIME_WORD_RE.finditer(body):
        word = fold(m.group(0)).lower().split()[-1]
        if word not in ctx.fact_text:
            reasons.append(f"date_not_in_facts:{m.group(0).strip()}")
    if UNVERIFIABLE_RE.search(body) or NEARBY_RE.search(body):
        reasons.append("unverifiable_wording")
    if BOUND_RE.search(PH.sub("{F}", raw)):
        reasons.append("bound_wording")
    if ORDINAL_RE.search(body):
        reasons.append("ordinal_wording")
    for m in TIME_NOUN_RE.finditer(body):
        if m.group(0).lower().rstrip("s") not in ctx.fact_text:
            reasons.append(f"unit_not_in_facts:{m.group(0).lower()}")
    if POLITICAL_RE.search(body):
        reasons.append("content_policy")
    if CAUSAL_RE.search(body):
        reasons.append("causal_wording")
    if ACCURACY_RE.search(body):
        reasons.append("accuracy_wording")
    if PREDICT_RE.search(body):
        reasons.append("prediction_wording")
    if VOCAB_RE.search(body):
        reasons.append("vocabulary")
    if HYPE_RE.search(body):
        reasons.append("hype_wording")
    if PERSON_RE.search(body):
        reasons.append("first_person")
    if "!" in body or EMOJI_RE.search(body) or MARKDOWN_RE.search(body):
        reasons.append("style")
    # de-duplicate, keep order
    seen, out = set(), []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z{])")


def split_statements(text: str) -> list[str]:
    return [s for s in (p.strip() for p in _SPLIT_RE.split(text or "")) if s]


def check_block(block: dict, ctx: Context) -> tuple[int, list[str]]:
    """(statements checked, reasons). A bullets block is one statement per item."""
    if block["kind"] == "paragraph":
        stmts = split_statements(block.get("text") or "")
    else:
        stmts = [s for s in (block.get("items") or []) if isinstance(s, str)]
    reasons: list[str] = []
    if not stmts:
        return 0, ["empty"]
    for s in stmts:
        reasons.extend(check_statement(s, ctx))
    total_words = sum(len(s.split()) for s in stmts)
    if total_words > MAX_BLOCK_WORDS:
        reasons.append("too_long")
    seen, out = set(), []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return len(stmts), out
