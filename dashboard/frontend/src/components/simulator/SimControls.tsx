import { colors } from "../../lib/tradingColors";

export interface SimParamsState {
  call_stop_buffer: number;
  put_stop_buffer: number;
  min_credit_call: number;
  min_credit_put: number;
  put_only_max_vix: number;
  max_entries: number;
  commission_per_leg: number;
  conditional_entries: boolean;
  downday_threshold_pct: number;
}

export const DEFAULT_PARAMS: SimParamsState = {
  call_stop_buffer: 0.35,
  put_stop_buffer: 1.55,
  min_credit_call: 135.0,
  min_credit_put: 210.0,
  put_only_max_vix: 25.0,
  max_entries: 3,
  commission_per_leg: 2.50,
  conditional_entries: true,
  downday_threshold_pct: 0.003,
};

const PRESETS: Record<string, { label: string; params: Partial<SimParamsState> }> = {
  current: { label: "Current", params: { ...DEFAULT_PARAMS } },
  conservative: {
    label: "Conservative",
    params: { put_stop_buffer: 3.0, min_credit_put: 300, max_entries: 2 },
  },
  aggressive: {
    label: "Aggressive",
    params: { put_stop_buffer: 1.0, min_credit_put: 180, min_credit_call: 100 },
  },
  tight_stops: {
    label: "Tight Stops",
    params: { call_stop_buffer: 0.15, put_stop_buffer: 0.75 },
  },
  wide_stops: {
    label: "Wide Stops",
    params: { call_stop_buffer: 0.60, put_stop_buffer: 3.0 },
  },
};

interface SliderProps {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  format: (v: number) => string;
  onChange: (v: number) => void;
  isModified: boolean;
}

function ParamSlider({ label, value, min, max, step, format, onChange, isModified }: SliderProps) {
  return (
    <div className="bg-card rounded-lg border border-border-dim p-3">
      <div className="flex items-center justify-between mb-2">
        <span className="text-[11px] text-text-secondary flex items-center gap-1.5">
          {isModified && (
            <span
              className="w-1.5 h-1.5 rounded-full inline-block"
              style={{ backgroundColor: colors.info }}
            />
          )}
          {label}
        </span>
        <span className="text-xs font-mono font-medium text-text-primary">
          {format(value)}
        </span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        className="w-full h-1.5 rounded-full appearance-none cursor-pointer"
        style={{
          background: `linear-gradient(to right, ${colors.info} 0%, ${colors.info} ${((value - min) / (max - min)) * 100}%, ${colors.borderDim} ${((value - min) / (max - min)) * 100}%, ${colors.borderDim} 100%)`,
        }}
      />
    </div>
  );
}

interface SimControlsProps {
  params: SimParamsState;
  onChange: (params: SimParamsState) => void;
  onRun: () => void;
  loading: boolean;
}

export function SimControls({ params, onChange, onRun, loading }: SimControlsProps) {
  const update = (key: keyof SimParamsState, value: number | boolean) => {
    onChange({ ...params, [key]: value });
  };

  const applyPreset = (presetKey: string) => {
    const preset = PRESETS[presetKey];
    if (!preset) return;
    onChange({ ...DEFAULT_PARAMS, ...preset.params });
  };

  const isModified = (key: keyof SimParamsState) =>
    params[key] !== DEFAULT_PARAMS[key];

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <h3 className="label-upper">Parameters</h3>
        <button
          onClick={onRun}
          disabled={loading}
          className="px-4 py-1.5 rounded text-xs font-medium transition-colors"
          style={{
            backgroundColor: loading ? colors.textDim : colors.info,
            color: loading ? colors.textSecondary : "#000",
          }}
        >
          {loading ? "Running..." : "Run Simulation"}
        </button>
      </div>

      {/* Presets */}
      <div className="flex gap-1.5 mb-3 flex-wrap">
        {Object.entries(PRESETS).map(([key, preset]) => (
          <button
            key={key}
            onClick={() => applyPreset(key)}
            className="px-2.5 py-1 rounded text-[10px] font-medium transition-colors border"
            style={{
              borderColor: colors.borderDim,
              color: colors.textSecondary,
              backgroundColor: "transparent",
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.borderColor = colors.info;
              e.currentTarget.style.color = colors.textPrimary;
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.borderColor = colors.borderDim;
              e.currentTarget.style.color = colors.textSecondary;
            }}
          >
            {preset.label}
          </button>
        ))}
      </div>

      {/* Sliders */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2">
        <ParamSlider
          label="Call Stop Buffer"
          value={params.call_stop_buffer}
          min={0} max={2} step={0.05}
          format={(v) => `$${v.toFixed(2)}`}
          onChange={(v) => update("call_stop_buffer", v)}
          isModified={isModified("call_stop_buffer")}
        />
        <ParamSlider
          label="Put Stop Buffer"
          value={params.put_stop_buffer}
          min={0} max={10} step={0.25}
          format={(v) => `$${v.toFixed(2)}`}
          onChange={(v) => update("put_stop_buffer", v)}
          isModified={isModified("put_stop_buffer")}
        />
        <ParamSlider
          label="Call Credit Gate"
          value={params.min_credit_call}
          min={0} max={200} step={5}
          format={(v) => `$${v.toFixed(0)}`}
          onChange={(v) => update("min_credit_call", v)}
          isModified={isModified("min_credit_call")}
        />
        <ParamSlider
          label="Put Credit Gate"
          value={params.min_credit_put}
          min={50} max={500} step={10}
          format={(v) => `$${v.toFixed(0)}`}
          onChange={(v) => update("min_credit_put", v)}
          isModified={isModified("min_credit_put")}
        />
        <ParamSlider
          label="Put-Only Max VIX"
          value={params.put_only_max_vix}
          min={10} max={50} step={0.5}
          format={(v) => v.toFixed(1)}
          onChange={(v) => update("put_only_max_vix", v)}
          isModified={isModified("put_only_max_vix")}
        />
        <ParamSlider
          label="Max Entries/Day"
          value={params.max_entries}
          min={1} max={7} step={1}
          format={(v) => String(v)}
          onChange={(v) => update("max_entries", v)}
          isModified={isModified("max_entries")}
        />
        <ParamSlider
          label="Down-Day Threshold"
          value={params.downday_threshold_pct}
          min={0.001} max={0.02} step={0.0005}
          format={(v) => `${(v * 100).toFixed(2)}%`}
          onChange={(v) => update("downday_threshold_pct", v)}
          isModified={isModified("downday_threshold_pct")}
        />
        <ParamSlider
          label="Commission/Leg"
          value={params.commission_per_leg}
          min={0} max={5} step={0.5}
          format={(v) => `$${v.toFixed(2)}`}
          onChange={(v) => update("commission_per_leg", v)}
          isModified={isModified("commission_per_leg")}
        />
      </div>

      {/* Toggle */}
      <div className="flex items-center gap-3 mt-2">
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={params.conditional_entries}
            onChange={(e) => update("conditional_entries", e.target.checked)}
            className="rounded"
          />
          <span className="text-[11px] text-text-secondary">
            Conditional entries (E6/E7)
          </span>
        </label>
      </div>
    </div>
  );
}
