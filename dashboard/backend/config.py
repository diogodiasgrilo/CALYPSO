"""Dashboard configuration via environment variables with sensible defaults."""

from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Base path for CALYPSO installation
    calypso_root: Path = Path("/opt/calypso")

    # Data files (HYDRA writes these, dashboard reads)
    hydra_state_file: Path = Path("/opt/calypso/data/hydra_state.json")
    hydra_metrics_file: Path = Path("/opt/calypso/data/hydra_metrics.json")
    backtesting_db: Path = Path("/opt/calypso/data/backtesting.db")
    position_registry_file: Path = Path("/opt/calypso/data/position_registry.json")

    # Log file
    hydra_log_file: Path = Path("/opt/calypso/logs/hydra/bot.log")

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
