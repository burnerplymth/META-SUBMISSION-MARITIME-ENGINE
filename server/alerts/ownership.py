"""Ownership and limits for alerts & watchlists.

Without ownership, rules and watchlists would be global, anonymous and
uncapped: anyone could create rules without credentials and delete
another browser's rule. This layer prevents that:

  * Owner token per browser: a random ``own_…`` token carried in the
    ``supermap_owner`` cookie, minted on first write (see
    ``resolve_owner``) and STAMPED on the rule/watchlist it creates
    (``owner`` field, written by AlertsStore.upsert_*).
  * Deletes are owner-checked: the stored owner must match the
    caller's owner cookie, OR the caller must present the
    ``SUPERMAP_ADMIN_TOKEN`` environment secret (admin override).
  * Legacy entries without an ``owner`` field stay visible to everyone
    and deletable by anyone — locking them would orphan those rules,
    and the data is a local demo store, not a multi-tenant service.
    New writes are always owned once the API layer passes the cookie
    token through (see below).
  * Caps: ``MAX_RULES_PER_OWNER`` rules per owner and
    ``MAX_WATCHLIST_NODES`` node ids per watchlist, enforced by
    AlertsStore on every upsert; past the cap the store raises
    RuleValidationError (code "invalid_request"), which the API maps
    to a clean HTTP 422 — never a silent accept, never a 500.

WIRING (server/api/app.py): POST handlers
call ``resolve_owner(req.cookies.get(COOKIE_NAME))``, pass the token
as ``owner=`` to the store upserts, and set the cookie on the
response when ``minted`` is true (HttpOnly, SameSite=Lax, Path=/,
Secure on https requests); PUT / node add-remove handlers gate on
``authorize_delete`` against the stored owner (admin override via
the ``x-admin-token`` header) and preserve the stored owner; DELETE
handlers pass ``owner=`` (the cookie, never minted on a delete) and
``admin_token=req.headers.get("x-admin-token")`` to ``delete_rule`` /
``delete_watchlist`` and map OwnershipError (code "forbidden") to
HTTP 403 in the standard error shape. Same-origin UI fetches carry the
cookie automatically.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets

COOKIE_NAME = "supermap_owner"
ADMIN_TOKEN_ENV = "SUPERMAP_ADMIN_TOKEN"
ADMIN_HEADER = "x-admin-token"
MAX_RULES_PER_OWNER = 50
MAX_WATCHLIST_NODES = 500
# The per-owner cap alone is not a bound. The owner is a cookie the
# CLIENT chooses (or omits: every cookie-less create mints a fresh owner),
# so one client could create unlimited rules and watchlists; every request
# then re-reads the whole append-only file and the evaluator walks every
# enabled rule. These are store-wide ceilings on LIVE records.
def _env_cap(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ[name]))
    except (KeyError, ValueError):
        return default


MAX_RULES_TOTAL = _env_cap("SUPERMAP_ALERTS_MAX_RULES", 500)
MAX_WATCHLISTS_TOTAL = _env_cap("SUPERMAP_ALERTS_MAX_WATCHLISTS", 200)


class OwnershipError(ValueError):
    """A delete was attempted without the owner cookie or admin token.
    ``code`` follows the alerts error-shape convention (the API layer
    maps "forbidden" to HTTP 403)."""

    def __init__(self, message: str):
        super().__init__(message)
        self.code = "forbidden"
        self.message = message


# The cookie token is a bearer
# secret, so it is NEVER stored or served in clear. The records carry
# ``owner = owner_key(token)`` (a one-way SHA-256 digest, prefix ``oh_``);
# ownership checks hash the presented cookie and compare in constant
# time. A leaked listing therefore cannot be replayed as a cookie.
# Older records hold the raw ``own_...`` token;
# ``AlertsStore.migrate_owner_tokens`` rewrites them on startup and
# ``owner_matches`` still honours a raw value until that has run.
OWNER_KEY_PREFIX = "oh_"
_OWNER_KEY_DOMAIN = b"supermap-owner-v1:"


class OwnerKey(str):
    """An already-hashed owner key (never a cookie token). The store
    passes it through ``owner_key`` untouched, so code that re-writes an
    owned record can hand the stored key back without double hashing."""


def owner_key(token) -> str | None:
    """One-way storage form of an owner cookie token."""
    if token is None or token == "":
        return None
    if isinstance(token, OwnerKey):
        return str(token)
    digest = hashlib.sha256(_OWNER_KEY_DOMAIN + str(token).encode("utf-8"))
    return OWNER_KEY_PREFIX + digest.hexdigest()[:40]


def is_owner_key(value) -> bool:
    return isinstance(value, str) and value.startswith(OWNER_KEY_PREFIX)


def owner_matches(stored_owner, presented_token) -> bool:
    """True iff ``presented_token`` (a cookie value) owns a record whose
    stored ``owner`` is ``stored_owner``. Constant-time; a presented
    value is ALWAYS hashed (a client cannot authenticate by sending a
    stored key as its cookie). A not-yet-migrated raw token on disk is
    compared directly."""
    if not stored_owner or not isinstance(presented_token, str) \
            or not presented_token:
        return False
    if is_owner_key(stored_owner):
        return hmac.compare_digest(stored_owner.encode("utf-8"),
                                   owner_key(presented_token).encode("utf-8"))
    return hmac.compare_digest(str(stored_owner).encode("utf-8"),
                               presented_token.encode("utf-8"))


def public_record(entry: dict, presented_token=None) -> dict:
    """The serialisable view of a rule/watchlist: the stored ``owner``
    key is dropped (it never leaves the server) and replaced by a
    boolean ``mine`` — true iff ``presented_token`` owns the record."""
    out = {k: v for k, v in entry.items() if k != "owner"}
    out["mine"] = owner_matches(entry.get("owner"), presented_token)
    return out


def new_owner_token() -> str:
    return "own_" + secrets.token_hex(16)


def resolve_owner(cookie_value: str | None) -> tuple[str, bool]:
    """(owner_token, minted): reuse a well-formed cookie token, else
    mint a fresh one (the caller sets it as a cookie on the response
    when ``minted`` is true)."""
    if isinstance(cookie_value, str) and cookie_value.startswith("own_") \
            and len(cookie_value) >= 20:
        return cookie_value, False
    return new_owner_token(), True


def is_admin(presented: str | None, *, env: dict | None = None) -> bool:
    """True iff ``presented`` matches the SUPERMAP_ADMIN_TOKEN secret.
    No token configured -> no admin override exists (fail closed)."""
    expected = (env if env is not None else os.environ).get(ADMIN_TOKEN_ENV)
    if not expected or not isinstance(presented, str) or not presented:
        return False
    return hmac.compare_digest(presented.encode("utf-8"),
                               expected.encode("utf-8"))


def authorize_delete(entry: dict, owner: str | None = None,
                     admin_token: str | None = None) -> None:
    """Raise OwnershipError unless the caller may delete ``entry``:
    legacy unowned entries are grandfathered (anyone may delete);
    owned entries need the matching owner token or the admin token."""
    entry_owner = entry.get("owner")
    if not entry_owner:
        return  # grandfathered legacy entry — see module docstring
    if owner is not None and owner_matches(entry_owner, owner):
        return
    if is_admin(admin_token):
        return
    raise OwnershipError(
        "this entry belongs to another browser; deleting it requires "
        "its owner cookie or the admin token")
