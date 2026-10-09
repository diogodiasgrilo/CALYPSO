"""Every variant must own its OWN log directory — and the live seat's metrics
epoch must agree with the dashboard's baseline for the same variant.

Found 2026-10-09, three hours before `bl`'s first live slot. `bl` was created by
copying `b`, and inherited **two** keys that must never be copied:

    "logging":  {"log_dir": "logs/hydra_variant_b"}   <- B-A's directory
    "strategy": {"metrics_epoch_date": "2026-07-24"}  <- B's C->B swap date

The log one is the dangerous half. `logger_service` resolves the file as
`log_file` -> `log_dir` -> `HYDRA_VARIANT_ID`, so the config key WINS over the
env var: `bl` wrote `ACCOUNT-ASSERT OK: variant BL` into
`logs/hydra_variant_b/bot.log` (verified on the VM, 2 lines), and
`logs/hydra_variant_bl/` stayed empty. Three consequences:

  1. the dashboard tails `variant_bl_log_file` for the LIVE seat -> empty panel;
  2. two processes share one `TimedRotatingFileHandler` target, so the midnight
     rollover has two renamers racing for one file;
  3. `trade_log_file` is `log_dir / "trades.json"` -- so `bl`'s REAL paper
     trades and `b`'s SIMULATED ones would append to one file. Mixing a traded
     era with a simulated one in a single record is the specific failure the
     metrics epoch exists to undo.

`bm` -- the real-money variant -- had the same inherited value (pre-existing,
not from the bl copy). There it means a real-money log and a real-money
`trades.json` landing in a PAPER variant's directory.

## Why the existing suite missed it

`test_variant_bl_live_seat_2026_10_09.py` asserts bl is an exact copy of b and
flags every DIVERGENCE. This defect is the opposite shape: two keys that are
wrong *because* they are identical. A diff-based test cannot see it, so the
invariant has to be stated against the variant's own id instead.
"""

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CFG = ROOT / "bots/hydra/config"
CONFIGS = sorted(CFG.glob("config_variant_*.json"))


def _vid(path: Path) -> str:
    return re.match(r"config_variant_(.+)\.json", path.name).group(1)


def _cfg(path: Path) -> dict:
    return json.loads(path.read_text())


VARIANTS = [(_vid(p), p) for p in CONFIGS]
assert VARIANTS, "no variant configs found — the sweep would pass vacuously"


class TestEachVariantOwnsItsLogDirectory:

    @pytest.mark.parametrize("vid,path", VARIANTS, ids=[v for v, _ in VARIANTS])
    def test_log_dir_matches_the_variants_own_id(self, vid, path):
        log_dir = (_cfg(path).get("logging") or {}).get("log_dir")
        assert log_dir, f"{vid}: no logging.log_dir — would fall through to the env var"
        assert log_dir == f"logs/hydra_variant_{vid}", (
            f"{vid} logs into {log_dir!r}; the config key BEATS HYDRA_VARIANT_ID"
        )

    def test_no_two_variants_share_a_log_directory(self):
        """Shared dir == shared bot.log AND shared trades.json."""
        owners: dict = {}
        for vid, path in VARIANTS:
            owners.setdefault((_cfg(path).get("logging") or {}).get("log_dir"), []).append(vid)
        shared = {d: v for d, v in owners.items() if len(v) > 1}
        assert not shared, f"variants sharing a log directory: {shared}"

    def test_the_live_seat_is_not_sharing_with_a_dry_run_variant(self):
        """The sharpest form: a traded log mixed with a simulated one."""
        live = [v for v, p in VARIANTS if _cfg(p).get("dry_run") is False]
        assert len(live) == 1, f"expected exactly one live seat, got {live}"
        live_dir = (_cfg(dict(VARIANTS)[live[0]]).get("logging") or {}).get("log_dir")
        others = {v: (_cfg(p).get("logging") or {}).get("log_dir")
                  for v, p in VARIANTS if v != live[0]}
        assert live_dir not in others.values(), (
            f"live seat {live[0]} shares {live_dir} with "
            f"{[v for v, d in others.items() if d == live_dir]}"
        )


class TestTheEpochAgreesWithTheDashboard:
    """The 2026-10-06 invariant: the metrics FILE's era and the dashboard's era
    for the same variant must move together, or the same number means two
    different things on two surfaces."""

    def _baselines(self) -> dict:
        src = (ROOT / "dashboard/backend/config.py").read_text()
        return dict(re.findall(r"variant_(\w+)_baseline_date:\s*str\s*=\s*\"([\d-]+)\"", src))

    def test_the_live_seats_epoch_equals_its_dashboard_baseline(self):
        live = [v for v, p in VARIANTS if _cfg(p).get("dry_run") is False]
        assert len(live) == 1, live
        vid = live[0]
        cfg_epoch = _cfg(dict(VARIANTS)[vid])["strategy"].get("metrics_epoch_date")
        dash = self._baselines().get(vid)
        assert dash, f"no variant_{vid}_baseline_date in the dashboard config"
        assert cfg_epoch == dash, (
            f"{vid}: config epoch {cfg_epoch!r} != dashboard baseline {dash!r}"
        )

    def test_bl_did_not_keep_bs_epoch(self):
        """bl has no record before 2026-10-09, so b's swap date is not its epoch."""
        assert _cfg(CFG / "config_variant_bl.json")["strategy"]["metrics_epoch_date"] \
            == "2026-10-09"

    def test_b_keeps_its_own_epoch(self):
        """The fix must not disturb the variant whose era genuinely starts then."""
        assert _cfg(CFG / "config_variant_b.json")["strategy"]["metrics_epoch_date"] \
            == "2026-07-24"


class TestTheRealMoneyVariantIsNotPointedAtPaper:

    def test_bm_does_not_log_into_a_paper_variants_directory(self):
        bm = CFG / "config_variant_bm.json"
        if not bm.exists():
            pytest.skip("bm config not present on this branch")
        log_dir = (_cfg(bm).get("logging") or {}).get("log_dir")
        assert log_dir == "logs/hydra_variant_bm", log_dir
        paper = {(_cfg(p).get("logging") or {}).get("log_dir")
                 for v, p in VARIANTS if v != "bm"}
        assert log_dir not in paper, (
            f"real-money logs + trades.json would land in a PAPER dir: {log_dir}"
        )
