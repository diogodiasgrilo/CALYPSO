"""HYDRA Strategy Simulator — replays historical days with modified config parameters.

Answers: "What would have happened if I used these settings instead?"

Two simulation tiers:
  - Tier 1 (days with spread_snapshots): Full tick-by-tick stop replay
  - Tier 2 (days without): Heuristic from trade_stops actual_debit vs new stop level
"""

import logging
from asyncio import to_thread
from dataclasses import dataclass, field, asdict
from typing import Optional

from dashboard.backend.services.db_reader import BacktestingDBReader

logger = logging.getLogger("dashboard.simulator")

# Minimum trading days with spread_snapshots before simulator unlocks
REQUIRED_FULL_SIM_DAYS = 30

# Date from which spread_snapshots + credit data are populated
DATA_START_DATE = "2026-03-11"


@dataclass
class SimParams:
    """Simulator input parameters — the config knobs."""
    call_stop_buffer: float = 0.35       # call-side buffer ($/contract)
    put_stop_buffer: float = 1.55       # put-side buffer ($/contract)
    min_credit_call: float = 200.0      # call credit gate ($ total)
    min_credit_put: float = 275.0       # put credit gate ($ total)
    put_only_max_vix: float = 15.0      # MKT-032 VIX gate for put-only
    max_entries: int = 3                # max base entries per day
    commission_per_leg: float = 2.50    # per-leg commission
    conditional_entries: bool = True     # E6/E7 enabled
    downday_threshold_pct: float = 0.003  # MKT-035 downday threshold (0.3%)
    upday_threshold_pct: float = 0.0025   # Upday-035 threshold (0.25%)


@dataclass
class SimEntryResult:
    """Simulation result for a single entry."""
    entry_number: int
    actual_type: str              # "Iron Condor", "Put Spread", "Call Spread"
    simulated_type: str           # may change if credit gate reclassifies
    actual_pnl: float
    simulated_pnl: float
    actual_stopped: bool
    simulated_stopped: bool       # any side stopped
    call_stopped: bool = False
    put_stopped: bool = False
    sim_call_stopped: bool = False
    sim_put_stopped: bool = False
    skipped: bool = False         # entry removed by credit gate or max_entries
    newly_included: bool = False  # entry was skipped but now included
    note: str = ""


@dataclass
class SimDayResult:
    """Simulation result for a single day."""
    date: str
    actual_net_pnl: float
    simulated_net_pnl: float
    delta_pnl: float
    actual_entries: int
    simulated_entries: int
    actual_stops: int
    simulated_stops: int
    simulation_tier: int           # 1 = full spread data, 2 = heuristic
    entries: list[SimEntryResult] = field(default_factory=list)


@dataclass
class SimResult:
    """Complete simulation result."""
    days: list[SimDayResult]
    actual_total_pnl: float
    simulated_total_pnl: float
    delta_total_pnl: float
    actual_win_rate: float
    simulated_win_rate: float
    actual_max_drawdown: float
    simulated_max_drawdown: float
    actual_sharpe: float
    simulated_sharpe: float
    actual_avg_pnl: float
    simulated_avg_pnl: float
    actual_total_stops: int
    simulated_total_stops: int
    total_days: int
    tier1_days: int
    tier2_days: int
    params: dict


class SimulatorEngine:
    """Replays historical trading days with modified parameters."""

    def __init__(self, db_reader: BacktestingDBReader):
        self._db = db_reader
        self._entries: list[dict] = []
        self._stops: list[dict] = []
        self._skipped: list[dict] = []
        self._summaries: list[dict] = []
        self._snapshots: dict[str, list[dict]] = {}  # date -> snapshots
        self._ohlc: dict[str, list[dict]] = {}        # date -> ohlc bars
        self._loaded = False

    async def load_data(self) -> None:
        """Load all historical data into memory (cached)."""
        if self._loaded:
            return

        logger.info("Simulator: loading historical data from SQLite")

        self._entries = await self._db.get_all_entries()
        self._stops = await self._db.get_all_stops()
        self._summaries = await self._db.get_all_summaries()

        # Load skipped entries
        self._skipped = await to_thread(
            self._db._query,
            "SELECT * FROM skipped_entries ORDER BY date, entry_number",
        )

        # Load spread snapshots (only days that have them)
        all_snapshots = await to_thread(
            self._db._query,
            "SELECT * FROM spread_snapshots ORDER BY timestamp, entry_number",
        )
        self._snapshots = {}
        for snap in all_snapshots:
            date = snap["timestamp"][:10]
            self._snapshots.setdefault(date, []).append(snap)

        # Load OHLC for MKT-035 recalculation
        all_ohlc = await to_thread(
            self._db._query,
            "SELECT * FROM market_ohlc_1min ORDER BY timestamp",
        )
        self._ohlc = {}
        for bar in all_ohlc:
            date = bar["timestamp"][:10]
            self._ohlc.setdefault(date, []).append(bar)

        self._loaded = True
        logger.info(
            f"Simulator: loaded {len(self._entries)} entries, "
            f"{len(self._stops)} stops, {len(self._summaries)} summaries, "
            f"{len(self._snapshots)} days with spread snapshots"
        )

    def invalidate_cache(self) -> None:
        """Force data reload on next simulation (call after HOMER runs)."""
        self._loaded = False

    async def get_status(self) -> dict:
        """Return data availability for the countdown."""
        await self.load_data()

        # Count days with spread_snapshots from DATA_START_DATE onwards
        full_sim_dates = sorted(
            d for d in self._snapshots if d >= DATA_START_DATE
        )
        all_dates = sorted(
            s["date"] for s in self._summaries if s["date"] >= DATA_START_DATE
        )

        return {
            "required_days": REQUIRED_FULL_SIM_DAYS,
            "full_sim_days": len(full_sim_dates),
            "total_trading_days": len(all_dates),
            "data_start_date": DATA_START_DATE,
            "full_sim_dates": full_sim_dates,
            "all_dates": all_dates,
            "ready": len(full_sim_dates) >= REQUIRED_FULL_SIM_DAYS,
            "days_remaining": max(0, REQUIRED_FULL_SIM_DAYS - len(full_sim_dates)),
        }

    async def simulate(self, params: SimParams) -> dict:
        """Run simulation across all historical days."""
        await self.load_data()

        # Group data by date
        entries_by_date: dict[str, list[dict]] = {}
        for e in self._entries:
            entries_by_date.setdefault(e["date"], []).append(e)

        stops_by_date: dict[str, list[dict]] = {}
        for s in self._stops:
            key = (s["date"], s["entry_number"], s["side"])
            stops_by_date.setdefault(s["date"], {})[key] = s

        skipped_by_date: dict[str, list[dict]] = {}
        for s in self._skipped:
            skipped_by_date.setdefault(s["date"], []).append(s)

        # Simulate each day
        day_results: list[SimDayResult] = []

        for summary in self._summaries:
            date = summary["date"]

            # Only simulate days with reliable data
            if date < DATA_START_DATE:
                continue

            entries = entries_by_date.get(date, [])
            stops_map = stops_by_date.get(date, {})
            skipped = skipped_by_date.get(date, [])
            snapshots = self._snapshots.get(date, [])
            ohlc = self._ohlc.get(date, [])
            has_snapshots = len(snapshots) > 0

            # Check if this day has per-entry credit data
            has_credit_data = any(
                (e.get("call_credit") is not None and e["call_credit"] > 0) or
                (e.get("put_credit") is not None and e["put_credit"] > 0)
                for e in entries
            )

            if not has_credit_data:
                # No credit data — can't simulate, pass through as-is
                actual_pnl = summary.get("net_pnl") or 0
                day_results.append(SimDayResult(
                    date=date,
                    actual_net_pnl=round(actual_pnl, 2),
                    simulated_net_pnl=round(actual_pnl, 2),
                    delta_pnl=0,
                    actual_entries=len(entries),
                    simulated_entries=len(entries),
                    actual_stops=sum(
                        1 for e in entries
                        if any(stops_map.get((date, e["entry_number"], s))
                               for s in ("call", "put"))
                    ),
                    simulated_stops=sum(
                        1 for e in entries
                        if any(stops_map.get((date, e["entry_number"], s))
                               for s in ("call", "put"))
                    ),
                    simulation_tier=0,  # 0 = no simulation possible
                ))
                continue

            day_result = self._simulate_day(
                date, entries, stops_map, skipped, snapshots, ohlc,
                summary, params, has_snapshots,
            )
            day_results.append(day_result)

        return self._build_result(day_results, params)

    def _simulate_day(
        self,
        date: str,
        entries: list[dict],
        stops_map: dict,
        skipped: list[dict],
        snapshots: list[dict],
        ohlc: list[dict],
        summary: dict,
        params: SimParams,
        has_snapshots: bool,
    ) -> SimDayResult:
        """Simulate a single trading day."""
        actual_net = summary.get("net_pnl") or 0
        sim_entries: list[SimEntryResult] = []
        sim_total_pnl = 0.0
        sim_stop_count = 0
        actual_stop_count = 0

        # Group snapshots by entry_number
        snaps_by_entry: dict[int, list[dict]] = {}
        for snap in snapshots:
            snaps_by_entry.setdefault(snap["entry_number"], []).append(snap)

        for entry in entries:
            enum = entry["entry_number"]
            etype = entry.get("entry_type") or "Iron Condor"
            call_credit = entry.get("call_credit") or 0
            put_credit = entry.get("put_credit") or 0
            total_credit = call_credit + put_credit
            vix = entry.get("vix_at_entry") or 0

            # Check if actually stopped
            call_stop_rec = stops_map.get((date, enum, "call"))
            put_stop_rec = stops_map.get((date, enum, "put"))
            was_call_stopped = call_stop_rec is not None
            was_put_stopped = put_stop_rec is not None
            was_stopped = was_call_stopped or was_put_stopped
            if was_stopped:
                actual_stop_count += 1

            # --- Filter 1: max entries ---
            # Conditional entries: entry time >= 13:00 (afternoon slots are conditional)
            entry_time = entry.get("entry_time", "")
            is_conditional = entry_time >= "13:00" if entry_time else enum > 5
            if is_conditional and not params.conditional_entries:
                sim_entries.append(SimEntryResult(
                    entry_number=enum, actual_type=etype, simulated_type="skipped",
                    actual_pnl=self._calc_actual_entry_pnl(entry, call_stop_rec, put_stop_rec),
                    simulated_pnl=0, actual_stopped=was_stopped, simulated_stopped=False,
                    call_stopped=was_call_stopped, put_stopped=was_put_stopped,
                    skipped=True, note="conditional disabled",
                ))
                continue

            if not is_conditional and enum > params.max_entries:
                sim_entries.append(SimEntryResult(
                    entry_number=enum, actual_type=etype, simulated_type="skipped",
                    actual_pnl=self._calc_actual_entry_pnl(entry, call_stop_rec, put_stop_rec),
                    simulated_pnl=0, actual_stopped=was_stopped, simulated_stopped=False,
                    call_stopped=was_call_stopped, put_stopped=was_put_stopped,
                    skipped=True, note=f"exceeds max {params.max_entries} entries",
                ))
                continue

            # --- Filter 2: credit gate ---
            # Skip credit gate if credits are NULL (early data without credit columns)
            has_credit_data = entry.get("call_credit") is not None or entry.get("put_credit") is not None
            if has_credit_data and total_credit > 0:
                sim_type, gate_note = self._apply_credit_gate(
                    etype, call_credit, put_credit, vix, params
                )
            else:
                sim_type, gate_note = etype, ""
            if sim_type == "skipped":
                sim_entries.append(SimEntryResult(
                    entry_number=enum, actual_type=etype, simulated_type="skipped",
                    actual_pnl=self._calc_actual_entry_pnl(entry, call_stop_rec, put_stop_rec),
                    simulated_pnl=0, actual_stopped=was_stopped, simulated_stopped=False,
                    call_stopped=was_call_stopped, put_stopped=was_put_stopped,
                    skipped=True, note=gate_note,
                ))
                continue

            # --- Simulate stops ---
            entry_snaps = snaps_by_entry.get(enum, [])

            if not has_credit_data or total_credit <= 0:
                # No credit data — can't simulate stops, use actual outcome
                actual_entry_pnl = self._calc_actual_entry_pnl(entry, call_stop_rec, put_stop_rec)
                sim_result = {
                    "gross_pnl": actual_entry_pnl,
                    "sim_call_stopped": was_call_stopped,
                    "sim_put_stopped": was_put_stopped,
                    "note": "no credit data",
                }
            elif has_snapshots and entry_snaps:
                sim_result = self._simulate_entry_with_snapshots(
                    entry, sim_type, entry_snaps, params
                )
            else:
                sim_result = self._simulate_entry_heuristic(
                    entry, sim_type, call_stop_rec, put_stop_rec, params
                )

            # Calculate commission
            sides_opened = 2 if sim_type == "Iron Condor" else 1
            legs_opened = sides_opened * 2
            stopped_sides = (1 if sim_result["sim_call_stopped"] else 0) + \
                            (1 if sim_result["sim_put_stopped"] else 0)
            legs_closed = stopped_sides * 2
            commission = (legs_opened + legs_closed) * params.commission_per_leg

            sim_pnl = sim_result["gross_pnl"] - commission
            sim_total_pnl += sim_pnl
            if sim_result["sim_call_stopped"] or sim_result["sim_put_stopped"]:
                sim_stop_count += 1

            actual_pnl = self._calc_actual_entry_pnl(entry, call_stop_rec, put_stop_rec)

            sim_entries.append(SimEntryResult(
                entry_number=enum,
                actual_type=etype,
                simulated_type=sim_type,
                actual_pnl=actual_pnl,
                simulated_pnl=round(sim_pnl, 2),
                actual_stopped=was_stopped,
                simulated_stopped=sim_result["sim_call_stopped"] or sim_result["sim_put_stopped"],
                call_stopped=was_call_stopped,
                put_stopped=was_put_stopped,
                sim_call_stopped=sim_result["sim_call_stopped"],
                sim_put_stopped=sim_result["sim_put_stopped"],
                note=gate_note or sim_result.get("note", ""),
            ))

        return SimDayResult(
            date=date,
            actual_net_pnl=round(actual_net, 2),
            simulated_net_pnl=round(sim_total_pnl, 2),
            delta_pnl=round(sim_total_pnl - actual_net, 2),
            actual_entries=len(entries),
            simulated_entries=sum(1 for e in sim_entries if not e.skipped),
            actual_stops=actual_stop_count,
            simulated_stops=sim_stop_count,
            simulation_tier=1 if has_snapshots else 2,
            entries=sim_entries,
        )

    def _apply_credit_gate(
        self,
        entry_type: str,
        call_credit: float,
        put_credit: float,
        vix: float,
        params: SimParams,
    ) -> tuple[str, str]:
        """Re-evaluate credit gate with simulated parameters.

        Returns (simulated_entry_type, note_string).
        """
        call_viable = call_credit >= params.min_credit_call
        put_viable = put_credit >= params.min_credit_put

        if entry_type == "Put Spread":
            # Was put-only; check if put still viable
            if not put_viable:
                return "skipped", f"put ${put_credit:.0f} < gate ${params.min_credit_put:.0f}"
            if vix >= params.put_only_max_vix:
                return "skipped", f"VIX {vix:.1f} >= {params.put_only_max_vix:.1f}"
            return "Put Spread", ""

        if entry_type == "Call Spread":
            # Was call-only; check if call still viable
            if not call_viable:
                return "skipped", f"call ${call_credit:.0f} < gate ${params.min_credit_call:.0f}"
            return "Call Spread", ""

        # Full Iron Condor
        if call_viable and put_viable:
            return "Iron Condor", ""
        if not call_viable and put_viable:
            if vix >= params.put_only_max_vix:
                return "skipped", f"call non-viable, VIX {vix:.1f} >= {params.put_only_max_vix:.1f}"
            return "Put Spread", f"call ${call_credit:.0f} < gate → put-only"
        if call_viable and not put_viable:
            return "Call Spread", f"put ${put_credit:.0f} < gate → call-only"

        return "skipped", "both sides below credit gate"

    def _simulate_entry_with_snapshots(
        self,
        entry: dict,
        sim_type: str,
        snapshots: list[dict],
        params: SimParams,
    ) -> dict:
        """Tier 1: Full tick-by-tick stop simulation using spread snapshots."""
        call_credit = entry.get("call_credit") or 0
        put_credit = entry.get("put_credit") or 0
        total_credit = call_credit + put_credit

        # Calculate stop levels based on simulated entry type
        call_stop_level, put_stop_level = self._calc_stop_levels(
            sim_type, call_credit, put_credit, total_credit, params
        )

        sim_call_stopped = False
        sim_put_stopped = False
        call_stop_debit = 0.0
        put_stop_debit = 0.0

        for snap in snapshots:
            csv = snap.get("call_spread_value") or 0
            psv = snap.get("put_spread_value") or 0

            if call_stop_level is not None and not sim_call_stopped and csv >= call_stop_level:
                sim_call_stopped = True
                call_stop_debit = csv

            if put_stop_level is not None and not sim_put_stopped and psv >= put_stop_level:
                sim_put_stopped = True
                put_stop_debit = psv

        # Gross P&L
        gross_pnl = 0.0
        if sim_type in ("Iron Condor", "Call Spread"):
            gross_pnl += call_credit - (call_stop_debit if sim_call_stopped else 0)
        if sim_type in ("Iron Condor", "Put Spread"):
            gross_pnl += put_credit - (put_stop_debit if sim_put_stopped else 0)

        note = ""
        if sim_call_stopped and sim_put_stopped:
            note = "both sides stopped"
        elif sim_call_stopped:
            note = "call stopped"
        elif sim_put_stopped:
            note = "put stopped"

        return {
            "gross_pnl": gross_pnl,
            "sim_call_stopped": sim_call_stopped,
            "sim_put_stopped": sim_put_stopped,
            "note": note,
        }

    def _simulate_entry_heuristic(
        self,
        entry: dict,
        sim_type: str,
        call_stop_rec: Optional[dict],
        put_stop_rec: Optional[dict],
        params: SimParams,
    ) -> dict:
        """Tier 2: Heuristic stop simulation using actual trade_stops data.

        Logic:
          - If no actual stop → no sim stop (spread never breached any level)
          - If actual stop triggered:
            - New stop level > actual_debit → stop avoided (spread never reached new level)
            - New stop level <= actual_debit → stop still triggers at same debit
        """
        call_credit = entry.get("call_credit") or 0
        put_credit = entry.get("put_credit") or 0
        total_credit = call_credit + put_credit

        call_stop_level, put_stop_level = self._calc_stop_levels(
            sim_type, call_credit, put_credit, total_credit, params
        )

        sim_call_stopped = False
        sim_put_stopped = False
        call_stop_debit = 0.0
        put_stop_debit = 0.0

        # Call side
        if call_stop_level is not None and call_stop_rec is not None:
            actual_debit = call_stop_rec.get("actual_debit") or 0
            if actual_debit > 0 and call_stop_level <= actual_debit:
                # Spread reached the new level — stop still triggers
                sim_call_stopped = True
                call_stop_debit = actual_debit
            # else: wider buffer means stop avoided

        # Put side
        if put_stop_level is not None and put_stop_rec is not None:
            actual_debit = put_stop_rec.get("actual_debit") or 0
            if actual_debit > 0 and put_stop_level <= actual_debit:
                sim_put_stopped = True
                put_stop_debit = actual_debit

        # Gross P&L
        gross_pnl = 0.0
        if sim_type in ("Iron Condor", "Call Spread"):
            gross_pnl += call_credit - (call_stop_debit if sim_call_stopped else 0)
        if sim_type in ("Iron Condor", "Put Spread"):
            gross_pnl += put_credit - (put_stop_debit if sim_put_stopped else 0)

        notes = []
        if call_stop_rec and not sim_call_stopped:
            notes.append("call stop avoided~")
        if put_stop_rec and not sim_put_stopped:
            notes.append("put stop avoided~")
        if sim_call_stopped:
            notes.append("call stopped")
        if sim_put_stopped:
            notes.append("put stopped")

        return {
            "gross_pnl": gross_pnl,
            "sim_call_stopped": sim_call_stopped,
            "sim_put_stopped": sim_put_stopped,
            "note": ", ".join(notes),
        }

    def _calc_stop_levels(
        self,
        entry_type: str,
        call_credit: float,
        put_credit: float,
        total_credit: float,
        params: SimParams,
    ) -> tuple[Optional[float], Optional[float]]:
        """Calculate call and put stop levels for given entry type and params."""
        call_buffer = params.call_stop_buffer * 100
        put_buffer = params.put_stop_buffer * 100

        if entry_type == "Iron Condor":
            return (total_credit + call_buffer, total_credit + put_buffer)
        elif entry_type == "Put Spread":
            return (None, put_credit + put_buffer)
        elif entry_type == "Call Spread":
            # call_credit + theoretical $2.60 put + call buffer
            return (call_credit + 260 + call_buffer, None)
        return (None, None)

    def _calc_actual_entry_pnl(
        self,
        entry: dict,
        call_stop_rec: Optional[dict],
        put_stop_rec: Optional[dict],
    ) -> float:
        """Calculate actual P&L for an entry from DB records."""
        call_credit = entry.get("call_credit") or 0
        put_credit = entry.get("put_credit") or 0
        etype = entry.get("entry_type") or "Iron Condor"

        pnl = 0.0
        if etype in ("Iron Condor", "Call Spread"):
            if call_stop_rec:
                pnl += call_credit - (call_stop_rec.get("actual_debit") or 0)
            else:
                pnl += call_credit  # expired worthless = keep credit

        if etype in ("Iron Condor", "Put Spread"):
            if put_stop_rec:
                pnl += put_credit - (put_stop_rec.get("actual_debit") or 0)
            else:
                pnl += put_credit

        return round(pnl, 2)

    def _build_result(self, days: list[SimDayResult], params: SimParams) -> dict:
        """Compute aggregate metrics and return full result."""
        actual_pnls = [d.actual_net_pnl for d in days]
        sim_pnls = [d.simulated_net_pnl for d in days]

        tier1 = sum(1 for d in days if d.simulation_tier == 1)
        tier2 = sum(1 for d in days if d.simulation_tier == 2)

        result = SimResult(
            days=[asdict(d) for d in days],
            actual_total_pnl=round(sum(actual_pnls), 2),
            simulated_total_pnl=round(sum(sim_pnls), 2),
            delta_total_pnl=round(sum(sim_pnls) - sum(actual_pnls), 2),
            actual_win_rate=self._win_rate(actual_pnls),
            simulated_win_rate=self._win_rate(sim_pnls),
            actual_max_drawdown=self._max_drawdown(actual_pnls),
            simulated_max_drawdown=self._max_drawdown(sim_pnls),
            actual_sharpe=self._sharpe(actual_pnls),
            simulated_sharpe=self._sharpe(sim_pnls),
            actual_avg_pnl=round(sum(actual_pnls) / max(len(actual_pnls), 1), 2),
            simulated_avg_pnl=round(sum(sim_pnls) / max(len(sim_pnls), 1), 2),
            actual_total_stops=sum(d.actual_stops for d in days),
            simulated_total_stops=sum(d.simulated_stops for d in days),
            total_days=len(days),
            tier1_days=tier1,
            tier2_days=tier2,
            params=asdict(params),
        )
        return asdict(result)

    @staticmethod
    def _win_rate(pnls: list[float]) -> float:
        if not pnls:
            return 0.0
        wins = sum(1 for p in pnls if p > 0)
        return round(wins / len(pnls) * 100, 1)

    @staticmethod
    def _max_drawdown(pnls: list[float]) -> float:
        if not pnls:
            return 0.0
        peak = 0.0
        cumulative = 0.0
        max_dd = 0.0
        for p in pnls:
            cumulative += p
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd
        return round(-max_dd, 2)

    @staticmethod
    def _sharpe(pnls: list[float]) -> float:
        if len(pnls) < 2:
            return 0.0
        mean = sum(pnls) / len(pnls)
        variance = sum((p - mean) ** 2 for p in pnls) / (len(pnls) - 1)
        std = variance ** 0.5
        if std == 0:
            return 0.0
        return round((mean / std) * (252 ** 0.5), 2)
