"""Single source of truth for IBKR CP API field codes + default field sets.

Imported by both `shared.ib_client` and `shared.ib_streaming` to avoid the
maintenance hazard of duplicate constant blocks drifting apart silently.

Field codes are stable IBKR identifiers documented at
https://ibkrcampus.com/ibkr-api-page/cpapi-v1/#market-data-fields and
mirrored in ibind/client/ibkr_definitions.py.

Note 7633 (per-strike implied vol) vs 7283 (underlying-level implied vol).
We use 7633 because we read greeks at the leg level.
"""

from __future__ import annotations


# ─── Field codes ─────────────────────────────────────────────────────────────
FIELD_LAST = "31"
FIELD_BID = "84"
FIELD_ASK = "86"
FIELD_BID_SIZE = "88"
FIELD_ASK_SIZE = "85"
FIELD_MARK = "7635"
FIELD_DELTA = "7308"
FIELD_GAMMA = "7309"
FIELD_THETA = "7310"
FIELD_VEGA = "7311"
FIELD_IV = "7633"
FIELD_OI = "7638"
FIELD_AVAILABILITY = "6509"  # 'R' real-time / 'D' delayed / 'Z' stale


# ─── Default field sets ──────────────────────────────────────────────────────
DEFAULT_QUOTE_FIELDS = [
    FIELD_LAST, FIELD_BID, FIELD_ASK, FIELD_BID_SIZE, FIELD_ASK_SIZE,
    FIELD_MARK, FIELD_AVAILABILITY,
]
DEFAULT_GREEKS_FIELDS = [
    FIELD_DELTA, FIELD_GAMMA, FIELD_THETA, FIELD_VEGA, FIELD_IV, FIELD_OI,
]
DEFAULT_OPTION_QUOTE_FIELDS = DEFAULT_QUOTE_FIELDS + DEFAULT_GREEKS_FIELDS


# ─── IBKR-specific constants ─────────────────────────────────────────────────
# IBKR's published USD spread template conid — prefix for the `conidex`
# field on USD multi-leg combos (iron condors, vertical spreads).
SPREAD_TEMPLATE_CONID = 28812380
