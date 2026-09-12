"""Read a Brandon variant's defensive-overlay hedge sidecar
(``brandon_hedge_legs.json``) and return per-entry overlay summaries for the
dashboard. Pure-stdlib, missing-file-tolerant — mirrors ``dc_reader.py``.

WHY this exists: Brandon variant B stacks a *defensive overlay* (a debit spread
before 12:30 ET, a butterfly after) on top of an iron condor whenever a short
side is threatened. These structures regularly drive the MAJORITY of B's daily
P&L, but the dashboard never surfaced them — the entries panel only drew the IC
legs, so the overlay (and the P&L it produced) was invisible. This reader makes
them visible.

P&L note: the overlay's settlement P&L is NOT stored separately in the bot's DB
(it's folded into each entry's realized_pnl at settlement). So this reader
computes each overlay's value at a given SPX the SAME way the bot's hedge
settlement does — intrinsic value of the legs minus the debit paid — for display.
It is informational; the authoritative realized total still lives on the entry.

    overlay P&L = Σ_legs  sign · (intrinsic(SPX) − fill_price) · 100 · quantity
    where sign = +1 for a long leg, −1 for a short leg.
"""
import json
import os
from typing import Optional


def _intrinsic(contract_type: str, strike: float, spx: float) -> float:
    if contract_type == "call":
        return max(spx - strike, 0.0)
    return max(strike - spx, 0.0)  # put


def _leg_sign(side: str) -> float:
    return 1.0 if side == "long" else -1.0


def _overlay_debit(legs: list) -> float:
    """Net debit paid to open the structure (positive = you paid)."""
    total = 0.0
    for leg in legs:
        total += _leg_sign(leg.get("side")) * float(leg.get("fill_price", 0.0)) * 100.0 * float(leg.get("quantity", 0) or 0)
    return round(total, 2)


def _overlay_pnl(legs: list, spx: float) -> float:
    total = 0.0
    for leg in legs:
        intr = _intrinsic(leg.get("contract_type"), float(leg.get("strike", 0) or 0), spx)
        total += _leg_sign(leg.get("side")) * (intr - float(leg.get("fill_price", 0.0))) * 100.0 * float(leg.get("quantity", 0) or 0)
    return round(total, 2)


def read_overlays_by_entry(sidecar_path: Optional[str], spx: Optional[float]) -> dict:
    """Return ``{entry_number(str): [overlay, ...]}`` where each overlay is
    ``{structure, threatened_side, placed_at, debit, pnl, legs:[{side,contract_type,strike,quantity}]}``.

    An entry can carry more than one overlay (e.g. a call side threatened in the
    morning and a put side in the afternoon), so legs are grouped by placement.
    ``pnl`` is None when no usable SPX is available. Missing/broken file → {}.
    """
    if not sidecar_path or not os.path.exists(sidecar_path):
        return {}
    try:
        with open(sidecar_path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}

    legs_by_entry = data.get("legs_by_entry") or {}
    out: dict = {}
    for en, legs in legs_by_entry.items():
        if not legs:
            continue
        # Group this entry's legs into distinct overlays by placement (a single
        # placement shares placed_at + threatened_side + structure).
        groups: dict = {}
        for leg in legs:
            key = (leg.get("placed_at"), leg.get("threatened_side"), leg.get("structure"))
            groups.setdefault(key, []).append(leg)
        overlays = []
        for (placed_at, threatened, structure), glegs in groups.items():
            pnl = _overlay_pnl(glegs, spx) if (spx and spx > 0) else None
            overlays.append({
                "structure": structure,
                "threatened_side": threatened,
                "placed_at": placed_at,
                "debit": _overlay_debit(glegs),
                "pnl": pnl,
                "legs": [
                    {
                        "side": l.get("side"),
                        "contract_type": l.get("contract_type"),
                        "strike": l.get("strike"),
                        "quantity": l.get("quantity"),
                    }
                    for l in glegs
                ],
            })
        if overlays:
            out[str(en)] = overlays
    return out
