"""Brandon-Jones-style additions to HYDRA.

Modules in this package are the "Trojan Horse Iron Condor" enhancements:
take-profit at credit-decay threshold, GEX-aware strike placement and breach
exit, defensive overlays (debit spread / butterfly), and a narrow-spread
width rule. Each module is a pure data-in / decision-out unit so it can be
unit-tested without HYDRA's strategy harness, and each is integrated into
strategy.py behind a config feature flag so variant A's behavior is
unchanged when the flags are off.
"""
