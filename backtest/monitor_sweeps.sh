#!/bin/bash
# Monitor live progress of all 4 new combo sweeps

watch -n 1 '
echo "📊 HYDRA New Combo Parameter Sweep Progress ($(date +%H:%M:%S))"
echo "================================================================"
echo ""
echo "[1/4] CALL STOP BUFFER SWEEP (workers=3)"
if [ -f backtest/results/call_stop_buffer_new_combo_progress.txt ]; then
  cat backtest/results/call_stop_buffer_new_combo_progress.txt
else
  echo "  ⏳ Starting..."
fi
echo ""
echo "[2/4] DECAY START MULTIPLIER SWEEP (workers=3)"
if [ -f backtest/results/buffer_decay_start_mult_new_combo_progress.txt ]; then
  cat backtest/results/buffer_decay_start_mult_new_combo_progress.txt
else
  echo "  ⏳ Starting..."
fi
echo ""
echo "[3/4] DECAY HOURS SWEEP (workers=2)"
if [ -f backtest/results/buffer_decay_hours_new_combo_progress.txt ]; then
  cat backtest/results/buffer_decay_hours_new_combo_progress.txt
else
  echo "  ⏳ Starting..."
fi
echo ""
echo "[4/4] PUT-ONLY MAX VIX SWEEP (workers=2)"
if [ -f backtest/results/put_only_max_vix_new_combo_progress.txt ]; then
  cat backtest/results/put_only_max_vix_new_combo_progress.txt
else
  echo "  ⏳ Starting..."
fi
echo ""
echo "================================================================"
echo "📈 Total Workers: 10  |  Total Tests: 22  |  Est. Time: 12-14 min"
'
