"""Conditional-GET (ETag / 304) support for the polled read-only dashboard APIs.

WHY THIS EXISTS (measured 2026-11-16, after the close)
------------------------------------------------------
``/api/strategies/{id}/snapshot`` is polled every 2s by the main view and
``/api/strategies/groups/{id}/comparison`` every 2s by the comparison page. Each
response was **re-sent in full on every poll**, with no validator anywhere in the
backend:

    bl snapshot        64,521 B raw / 11,481 B gzip   -> 20.7 MB/hour
    ic_0dte comparison 93,511 B raw / 11,720 B gzip   -> 21.1 MB/hour

Both payloads are dominated by today's ACCUMULATING 1-min OHLC array (41 kB of
the snapshot's 64 kB at the close), so the cost grows through the session and is
worst on the slowest link. On a phone on cellular that is the single largest
avoidable cost in the product.

And it buys nothing: diffing two polls 2.5s apart found **exactly one** differing
field in the snapshot, and five in the group comparison — every one of them the
same key, ``state_file_age_seconds``, which is a CLOCK READING taken at response
time, not content. After the close *nothing* changes at all, so a dashboard left
open overnight re-downloads ~20 MB/hour of byte-identical data.

THE VOLATILE-KEY RULE
---------------------
The ETag is computed over the payload with every key named in ``volatile_keys``
pruned recursively, wherever it appears in the tree (the group payload carries one
per member: ``variants.A``, ``variants.BL``, ...). Pruning by NAME rather than by
dotted path is deliberate — it keeps working when a new strategy joins the group
or a new nesting level appears, which a path list would silently miss.

NOTHING IS WITHHELD BY A 304. A pruned key is excluded from the *validator*, not
from the response: callers pass the live value through ``extra_headers`` so it
rides on both the 200 and the 304. This is what makes the optimisation
information-preserving rather than a tradeoff.

CORRECTNESS
-----------
The validator is a hash OF THE CONTENT, so it cannot go stale: if any byte of the
non-volatile payload differs, the ETag differs and a full 200 is sent. There is no
TTL to tune and no invalidation to forget — the failure mode of a content ETag is
an unnecessary 200, never a wrong 304. That property is why this is safe to put on
a live trading view.

``Cache-Control: private, no-cache`` is load-bearing: ``no-cache`` means "store it
but revalidate before reuse", which is precisely what lets the browser turn the
next poll into a conditional request; ``no-store`` would disable the whole
mechanism and ``max-age`` would let a stale body be served WITHOUT asking, which
on this view would be wrong. ``private`` keeps authenticated payloads out of any
shared cache.

``W/`` MATTERS HERE, IT IS NOT DEFENSIVE BOILERPLATE: nginx gzips
``application/json`` (``nginx-dashboard-public.conf``: ``gzip on``) and on-the-fly
gzip downgrades a strong ETag to a weak one, so the validator the browser echoes
back really does arrive as ``W/"..."``. Comparison therefore strips the weakness
prefix before comparing — without that, every revalidation through the real proxy
would miss and the whole mechanism would be dead in production while passing
locally.

100% read-only. No write path, no server-side cache, no stored state: this module
holds no dict of past responses, so it cannot leak one strategy's payload into
another's response, and it costs no memory.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping, Optional

from fastapi import Request, Response
from fastapi.encoders import jsonable_encoder

# Keys whose value is a clock reading taken at response-build time rather than
# part of the content. Excluded from the validator; still sent in the body, and
# echoed in a header so a 304 withholds nothing. Measured, not guessed — see the
# module docstring.
SNAPSHOT_VOLATILE_KEYS: frozenset[str] = frozenset({"state_file_age_seconds"})

# Mirrors starlette.responses.JSONResponse.render exactly, so the bytes we hash
# and return are the bytes FastAPI would have sent. `allow_nan=False` is part of
# that match: a NaN already raises on this path today, and quietly starting to
# emit invalid JSON instead would be a worse outcome than the existing 500.
_JSON_KWARGS: dict[str, Any] = {
    "ensure_ascii": False,
    "allow_nan": False,
    "indent": None,
    "separators": (",", ":"),
}

_CACHE_CONTROL = "private, no-cache"


def _prune(value: Any, volatile: frozenset[str]) -> Any:
    """Return ``value`` with every key named in ``volatile`` removed, at any
    depth. Pure — the caller's payload is never mutated (it is still going out
    over the wire, so mutating it would delete the field from the response)."""
    if isinstance(value, Mapping):
        return {
            k: _prune(v, volatile)
            for k, v in value.items()
            if k not in volatile
        }
    if isinstance(value, (list, tuple)):
        return [_prune(v, volatile) for v in value]
    return value


def compute_etag(encoded_payload: Any, volatile_keys: Iterable[str] = ()) -> str:
    """The quoted, strong ETag for an already-``jsonable_encoder``ed payload.

    ``sort_keys=True`` makes the validator independent of dict insertion order,
    so a pure refactor that reorders how a payload is assembled does not
    invalidate every client's cache for no reason.
    """
    volatile = frozenset(volatile_keys)
    canonical = json.dumps(
        _prune(encoded_payload, volatile), sort_keys=True, default=str, **_JSON_KWARGS
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
    return f'"{digest}"'


def _normalise(tag: str) -> str:
    """Strip the weak-validator prefix and surrounding quotes for comparison."""
    tag = tag.strip()
    if tag[:2].upper() == "W/":
        tag = tag[2:].strip()
    return tag.strip('"')


def etag_matches(if_none_match: Optional[str], etag: str) -> bool:
    """RFC 9110 If-None-Match evaluation, weak comparison.

    Handles the comma-separated list form, the ``*`` wildcard, and the ``W/``
    prefix nginx's gzip adds (see the module docstring).
    """
    if not if_none_match:
        return False
    candidates = [c for c in if_none_match.split(",") if c.strip()]
    if any(c.strip() == "*" for c in candidates):
        return True
    target = _normalise(etag)
    return any(_normalise(c) == target for c in candidates)


def conditional_json(
    request: Request,
    payload: Any,
    *,
    volatile_keys: Iterable[str] = (),
    extra_headers: Optional[Mapping[str, str]] = None,
) -> Response:
    """Serve ``payload`` as JSON with an ETag, or ``304`` when it is unchanged.

    ``volatile_keys`` are excluded from the validator only — they are still in
    the body, and ``extra_headers`` is the channel for carrying their live value
    on a 304 so that no information is lost by the optimisation.
    """
    encoded = jsonable_encoder(payload)
    etag = compute_etag(encoded, volatile_keys)

    headers: dict[str, str] = {"ETag": etag, "Cache-Control": _CACHE_CONTROL}
    if extra_headers:
        headers.update({k: str(v) for k, v in extra_headers.items() if v is not None})

    if etag_matches(request.headers.get("if-none-match"), etag):
        # No body, and deliberately no Content-Type: a 304 must not claim to
        # carry an entity. Starlette omits Content-Length for 304 itself.
        return Response(status_code=304, headers=headers)

    body = json.dumps(encoded, **_JSON_KWARGS).encode("utf-8")
    return Response(content=body, media_type="application/json", headers=headers)
