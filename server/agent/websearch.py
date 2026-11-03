"""Web search results and citations: untrusted data, made safe and checkable.

Meta's server-side ``web_search`` tool returns hits to the model and url
citations in the answer. Everything that comes back is UNTRUSTED TEXT:

  * results are reduced here to {title, url, domain, snippet, date}: strings
    are stripped of control characters and capped, only http(s) URLs without
    credentials survive, anything else is dropped;
  * instruction-like text in a hit is detected (``flag_results``) and the turn
    is marked: while a turn holds flagged web text, no state-changing tool can
    even be proposed (see ``Thread.refuse_tool``). Whatever the model does, an
    action tool still needs the user's confirm click;
  * the answer is never rendered as HTML anywhere (the web client builds text
    nodes; the exports escape every character);
  * every web-derived claim must carry a numbered citation. ``uncited_blocks``
    is the code check behind the 'Uncited' flag: a turn that used web results
    and states a paragraph without a ``[n]`` marker is flagged.

Pure functions, no I/O.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

MAX_URL_CHARS = 600
MAX_TITLE_CHARS = 200
MAX_SNIPPET_CHARS = 400
MAX_SOURCES_PER_TURN = 12
MAX_CITATIONS = 12

_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏‪-‮⁦-⁩]")
_WS_RE = re.compile(r"\s+")
_MARK_RE = re.compile(r"\[(\d{1,2})\]")

# Instruction-like text in a web result. Broader than the tool-result check in
# session.py: web pages are written by strangers.
_WEB_INJECTION_RE = re.compile(
    r"ignore (all |any |the |your )?(previous|prior|above|earlier)|disregard (the |your |all )?"
    r"(system|previous|prior|instructions)|forget (all |your )?(previous|prior|instructions)|"
    r"you (must|should|need to|have to|are to) (now )?(call|run|execute|confirm|start|build|export|"
    r"apply|open|send|reveal|print|ignore)|"
    r"(call|run|execute|invoke|use) the (tool|function)|confirm (it|the action|all|every)|"
    r"system prompt|developer (message|prompt)|new instructions|jailbreak|\bsudo\b|"
    r"reveal (your|the) (key|prompt|instructions)|api[_ -]?key|<\s*/?\s*(system|tool|script|iframe)\b|"
    r"\bassistant\s*:|\bAI (assistant|model)[:,] (please|you)",
    re.I)

# Paragraphs derived from the app's own tools are not web claims.
_MODEL_DERIVED_RE = re.compile(
    r"\b(modelled|simulated|recorded script|run [a-z0-9_\-]{4,}|plan [a-z0-9_\-]{4,}|stream hash)\b", re.I)
_LABEL_PREFIX = "RECORDED SCRIPT"


def clean_text(v, limit: int) -> str:
    """Control characters out, whitespace collapsed, length capped."""
    if not isinstance(v, str):
        return ""
    s = _WS_RE.sub(" ", _CTRL_RE.sub("", v)).strip()
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def safe_url(u) -> str | None:
    """An http(s) URL with a host, no credentials, no control characters;
    otherwise None. The only form a link is ever built from."""
    if not isinstance(u, str):
        return None
    u = u.strip()
    if not u or len(u) > MAX_URL_CHARS or _CTRL_RE.search(u) or re.search(r"\s", u):
        return None
    try:
        p = urlparse(u)
    except ValueError:
        return None
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        return None
    return u


def domain_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


_DATE_RES = (
    re.compile(r"\b(20\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b"),
    re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? (\d{1,2}), (20\d{2})\b"),
)
_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def date_hint(snippet: str) -> str | None:
    """A publication date only when the snippet's opening states one (ISO or
    'Oct 5, 2025'); the API documents no date field, so nothing is guessed."""
    head = snippet[:48]
    m = _DATE_RES[0].search(head)
    if m:
        return m.group(0)
    m = _DATE_RES[1].search(head)
    if m:
        return f"{m.group(3)}-{_MONTHS[m.group(1)[:3].title()]:02d}-{int(m.group(2)):02d}"
    return None


def sanitise_results(raw: list, *, limit: int = MAX_SOURCES_PER_TURN) -> list[dict]:
    """Provider hits -> [{title, url, domain, snippet, date}], de-duplicated by
    URL, capped. Hits without a usable URL are dropped."""
    out, seen = [], set()
    for r in raw or []:
        if not isinstance(r, dict):
            continue
        url = safe_url(r.get("url"))
        if url is None or url in seen:
            continue
        seen.add(url)
        snippet = clean_text(r.get("snippet") or r.get("text") or "", MAX_SNIPPET_CHARS)
        dom = domain_of(url)
        out.append({"title": clean_text(r.get("title"), MAX_TITLE_CHARS) or dom,
                    "url": url, "domain": dom, "snippet": snippet or None,
                    "date": date_hint(snippet) if snippet else None})
        if len(out) >= limit:
            break
    return out


def flag_results(results: list[dict]) -> bool:
    """True when any hit carries instruction-like text."""
    return any(_WEB_INJECTION_RE.search(f"{r.get('title') or ''} {r.get('snippet') or ''}")
               for r in results)


def flag_text(text: str) -> bool:
    return bool(_WEB_INJECTION_RE.search(text or ""))


_DOMAIN = r"(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}"
_DOMAIN_MARK_RE = re.compile(r"\[(" + _DOMAIN + r"(?:\s*[,;]\s*" + _DOMAIN + r")*)\](?!\()")


def _find_source(dom: str, sources: list[dict]) -> dict | None:
    """A retrieved hit for a cited domain: exact, else a sub/parent domain."""
    dom = dom.lower().removeprefix("www.")
    for s in sources:
        if s["domain"] == dom:
            return s
    for s in sources:
        if s["domain"].endswith("." + dom) or dom.endswith("." + s["domain"]):
            return s
    return None


def build_citations(raw: list, text: str, sources: list[dict], retrieved_at: str
                    ) -> tuple[str, list[dict], int]:
    """Number the answer's citations and put ``[n]`` markers into the text.

    Two inputs, both reduced to hits the search really returned:
      * the provider's url annotations ``raw`` ([{url, title, start, end}], the
        documented ``url_citation`` shape; offsets into ``text``);
      * domain markers the model writes after a claim, ``[example.com]`` (the
        chat prompt asks for them because the live API returned no annotations).
        A marker is kept only when its domain is the domain of a
        retrieved hit; any other domain-shaped marker is removed (``dropped``).
    Numbers follow first appearance in the text. A URL that is not safe is
    never linked. Returns (marked_text, citations, dropped_markers).
    """
    by_url = {s["url"]: s for s in sources}
    events: list[tuple[int, int, str | None, str | None]] = []   # (start, end, url, title)
    for c in raw or []:
        if not isinstance(c, dict):
            continue
        url = safe_url(c.get("url"))
        if url is None:
            continue
        end = max(0, min(len(text), int(c.get("end") or 0)))
        events.append((end, end, url, clean_text(c.get("title"), MAX_TITLE_CHARS) or None))
    for m in _DOMAIN_MARK_RE.finditer(text):
        found = [_find_source(d.strip(), sources) for d in re.split(r"[,;]", m.group(1))]
        if not any(found):
            events.append((m.start(), m.end(), None, None))
            continue
        for k, src in enumerate(f for f in found if f):
            events.append((m.start() if k == 0 else m.end(), m.end(), src["url"], src["title"]))
    events.sort(key=lambda e: (e[0], e[1]))
    cites: list[dict] = []
    index: dict[str, int] = {}
    out: list[str] = []
    pos = 0
    dropped = 0
    for start, end, url, title in events:
        if start < pos:                      # overlaps a marker already replaced
            continue
        out.append(text[pos:start])
        if start != end and out and out[-1].endswith(" ") and url is None:
            out[-1] = out[-1][:-1]
        pos = end
        if url is None:
            dropped += 1
            continue
        if url not in index:
            if len(cites) >= MAX_CITATIONS:
                continue
            src = by_url.get(url, {})
            index[url] = len(cites) + 1
            cites.append({"n": index[url], "title": title or src.get("title") or domain_of(url),
                          "url": url, "domain": domain_of(url),
                          "date": src.get("date"), "snippet": src.get("snippet"),
                          "retrieved_at": retrieved_at})
        mark = f"[{index[url]}]"
        if not (out and out[-1].endswith(mark)):
            out.append(mark)
    out.append(text[pos:])
    return "".join(out), cites, dropped


def paragraphs(text: str) -> list[str]:
    return [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]


def uncited_blocks(text: str, web_used: bool, n_citations: int) -> list[int]:
    """Indices of paragraphs that state web facts without a citation.

    Applies only when the turn retrieved web results (``web_used``). A
    paragraph is exempt when it carries a valid ``[n]`` marker, is short (a
    heading or a label), is the recorded-script label, or speaks about the
    app's own modelled or simulated output."""
    if not web_used:
        return []
    flagged = []
    for i, p in enumerate(paragraphs(text)):
        if any(1 <= int(m) <= max(n_citations, 0) for m in _MARK_RE.findall(p)):
            continue
        body = p.strip()
        if body.startswith(_LABEL_PREFIX) and len(body.splitlines()) == 1:
            continue
        if len(body.split()) < 8 or body.endswith(":"):
            continue
        if _MODEL_DERIVED_RE.search(body):
            continue
        flagged.append(i)
    return flagged


def strip_markers(text: str) -> str:
    return re.sub(r"\s?\[\d{1,2}\]", "", text)
