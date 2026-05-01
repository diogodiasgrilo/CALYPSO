"""Dashboard configuration via environment variables with sensible defaults."""

from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Base path for CALYPSO installation (dashboard reads HYDRA's data from here)
    calypso_root: Path = Path("/opt/calypso")

    # Data files (HYDRA writes these, dashboard reads)
    hydra_state_file: Path = Path("/opt/calypso/data/hydra_state.json")
    hydra_metrics_file: Path = Path("/opt/calypso/data/hydra_metrics.json")
    backtesting_db: Path = Path("/opt/calypso/data/backtesting.db")
    position_registry_file: Path = Path("/opt/calypso/data/position_registry.json")

    # Log file
    hydra_log_file: Path = Path("/opt/calypso/logs/hydra/bot.log")

    # Comparison mode (head-to-head dry-run experiment).
    # When comparison_mode_enabled = True, the dashboard exposes:
    #   - /api/variants/* endpoints reading each variant's parallel state/metrics/db
    #   - /comparison page in the SPA (hidden from nav otherwise)
    # Each non-A variant is a second HYDRA process running in dry mode with a
    # different config (config_variant_<id>.json), writing to data/variant_<id>/.
    # The router builds its registry by enumerating variant_<id>_state_file fields
    # below — to add a variant D, add 5 fields here and the router picks it up.
    comparison_mode_enabled: bool = False

    # Variant A — canonical/live HYDRA (75pt baseline, no pivot).
    variant_a_label: str = "A (75pt baseline)"

    # Variant B (75pt + directional pivot, close stressed leg only)
    variant_b_state_file: Path = Path("/opt/calypso/data/variant_b/hydra_state.json")
    variant_b_metrics_file: Path = Path("/opt/calypso/data/variant_b/hydra_metrics.json")
    variant_b_backtesting_db: Path = Path("/opt/calypso/data/variant_b/backtesting.db")
    variant_b_log_file: Path = Path("/opt/calypso/logs/hydra_variant_b/bot.log")
    variant_b_config_file: Path = Path("/opt/calypso/bots/hydra/config/config_variant_b.json")
    variant_b_label: str = "B (pivot, stressed-only)"

    # Variant C (75pt + directional pivot, close both legs)
    variant_c_state_file: Path = Path("/opt/calypso/data/variant_c/hydra_state.json")
    variant_c_metrics_file: Path = Path("/opt/calypso/data/variant_c/hydra_metrics.json")
    variant_c_backtesting_db: Path = Path("/opt/calypso/data/variant_c/backtesting.db")
    variant_c_log_file: Path = Path("/opt/calypso/logs/hydra_variant_c/bot.log")
    variant_c_config_file: Path = Path("/opt/calypso/bots/hydra/config/config_variant_c.json")
    variant_c_label: str = "C (pivot, both-legs)"

    # Agent intel directories
    agent_intel_dir: Path = Path("/opt/calypso/intel")

    # API security
    api_key: str = ""

    # Polling intervals (seconds)
    state_poll_interval: float = 1.0
    metrics_poll_interval: float = 10.0
    db_poll_interval: float = 30.0
    log_poll_interval: float = 2.0
    market_status_interval: float = 60.0

    # WebSocket
    ws_heartbeat_interval: float = 25.0

    # Server
    host: str = "127.0.0.1"
    port: int = 8001
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://localhost:8080",
        "http://35.231.243.156:8080",
    ]

    model_config = {"env_prefix": "DASHBOARD_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
