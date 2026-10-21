"""Signal-ingest service — TradingView webhooks -> logged trade intents.

DRY-RUN ONLY, and structurally so: nothing in this package imports IBClient,
BrokerClient or any other trading code, exactly as `dashboard/` does not. It
cannot place an order because it has no way to. Going live is a deliberate
future change that must add both a broker dependency AND a mode flag, and it
should be reviewed as such rather than enabled by a config edit.

Why it exists before the signal source is settled: the receiver is
INDICATOR-AGNOSTIC. Whatever ends up feeding it, the job is the same — validate,
de-duplicate, apply caps, and record what would have been traded. Running it in
log-only mode is also how the open question gets answered: it turns "how many
signals per year?" from a guess into a table.
"""

__version__ = "0.1.0"
