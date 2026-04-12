#!/usr/bin/env python3
"""
Extended live vs backtest audit: Apr 7-11 full week
Uses production configuration: call=$2.00, put=$2.75, call_buffer=$0.35, put_buffer=$1.55
"""

import subprocess
import sys
from datetime import datetime

print("\n" + "="*80)
print("EXTENDED BACKTEST: APRIL 7-11, 2026 (FULL WEEK)")
print("="*80)
print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S ET')}")
print()

config_overrides = {
    "start_date": "2026-04-07",
    "end_date": "2026-04-11",
    "call_stop_buffer": 35,  # $0.35 in cents
    "put_stop_buffer": 155,  # $1.55 in cents
    "min_viable_credit_call": 200,  # $2.00
    "min_viable_credit_put": 275,  # $2.75
    "buffer_decay_start_mult": 2.10,
    "buffer_decay_hours": 2.0,
}

# Build command with all overrides
cmd = ["python", "-m", "backtest.engine"]
for key, value in config_overrides.items():
    cmd.extend([f"--{key}", str(value)])

print("Running backtest with parameters:")
for key, value in config_overrides.items():
    print(f"  {key}: {value}")
print()

try:
    result = subprocess.run(cmd, cwd="/Users/ddias/Desktop/CALYPSO/Git Repo", 
                          capture_output=False, text=True, timeout=600)
    if result.returncode == 0:
        print("\n" + "="*80)
        print("BACKTEST COMPLETED SUCCESSFULLY")
        print("="*80)
    else:
        print(f"\nBacktest failed with return code {result.returncode}")
        sys.exit(1)
except subprocess.TimeoutExpired:
    print("\nBacktest timed out after 600 seconds")
    sys.exit(1)
except Exception as e:
    print(f"\nError running backtest: {e}")
    sys.exit(1)

print(f"\nCompleted: {datetime.now().strftime('%Y-%m-%d %H:%M:%S ET')}")
