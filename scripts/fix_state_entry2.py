#!/usr/bin/env python3
"""Fix state file to include Entry #2 with correct data from Saxo."""
import json
from datetime import datetime

state_file = "/opt/calypso/data/meic_tf_state.json"
with open(state_file, "r") as f:
    state = json.load(f)

print(f"Before: realized_pnl={state.get('total_realized_pnl')}, entries={len(state.get('entries', []))}")

state["total_realized_pnl"] = -165.0
state["call_stops_triggered"] = 1
state["total_commission"] = 20.0

# Remove existing Entry #2 if any
entries = [e for e in state.get("entries", []) if e.get("entry_number") != 2]

# Add correct Entry #2 data from Saxo
entry_2 = {
    "entry_number": 2,
    "entry_time": "2026-02-05T10:35:00-05:00",
    "strategy_id": "meic_tf_20260205_002",
    "trend_signal": "bearish",
    "call_only": True,
    "put_only": False,
    "short_call_strike": 6840.0,
    "long_call_strike": 6890.0,
    "short_put_strike": 0.0,
    "long_put_strike": 0.0,
    "short_call_position_id": None,
    "long_call_position_id": None,
    "short_put_position_id": None,
    "long_put_position_id": None,
    "short_call_uic": None,
    "long_call_uic": None,
    "short_put_uic": None,
    "long_put_uic": None,
    "call_spread_credit": 570.0,
    "put_spread_credit": 0.0,
    "call_side_stop": 560.0,
    "put_side_stop": 0.0,
    "call_side_stopped": True,
    "put_side_stopped": False,
    "is_complete": True,
    "open_commission": 10.0,
    "close_commission": 10.0
}

entries.append(entry_2)
entries.sort(key=lambda e: e.get("entry_number", 999))
state["entries"] = entries
state["last_saved"] = datetime.now().isoformat()

with open(state_file, "w") as f:
    json.dump(state, f, indent=2)

print(f"After: realized_pnl={state['total_realized_pnl']}, entries={[e.get('entry_number') for e in entries]}")
