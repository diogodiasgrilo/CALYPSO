import { useMemo } from "react";
import { colors } from "../../lib/tradingColors";

interface DataPoint {
  vix_open?: number;
  day_range?: number;
  net_pnl?: number;
  entries_stopped?: number;
  entries_placed?: number;
}

interface CorrelationHeatmapProps {
  data: DataPoint[];
}

function pearson(x: number[], y: number[]): number | null {
  if (x.length !== y.length || x.length < 3) return null;
  const n = x.length;
  const meanX = x.reduce((a, b) => a + b, 0) / n;
  const meanY = y.reduce((a, b) => a + b, 0) / n;
  let num = 0, denX = 0, denY = 0;
  for (let i = 0; i < n; i++) {
    const dx = x[i] - meanX;
    const dy = y[i] - meanY;
    num += dx * dy;
    denX += dx * dx;
    denY += dy * dy;
  }
  const den = Math.sqrt(denX * denY);
  if (den === 0) return null; // constant data — correlation undefined
  return num / den;
}

function corrColor(r: number | null): string {
  if (r === null) return colors.textDim;
  if (r > 0.3) return colors.profit;
  if (r < -0.3) return colors.loss;
  return colors.textDim;
}

function corrBg(r: number | null): string {
  if (r === null) return "transparent";
  const abs = Math.abs(r);
  if (r > 0) return `${colors.profit}${Math.round(abs * 64).toString(16).padStart(2, "0")}`;
  if (r < 0) return `${colors.loss}${Math.round(abs * 64).toString(16).padStart(2, "0")}`;
  return "transparent";
}

const VARIABLES = ["VIX", "Range", "Net P&L", "Stops", "Entries"] as const;

export function CorrelationHeatmap({ data }: CorrelationHeatmapProps) {
  const matrix = useMemo(() => {
    const vectors: Record<string, number[]> = {
      VIX: [], Range: [], "Net P&L": [], Stops: [], Entries: [],
    };
    for (const d of data) {
      if (d.vix_open == null || d.net_pnl == null) continue;
      vectors.VIX.push(d.vix_open ?? 0);
      vectors.Range.push(d.day_range ?? 0);
      vectors["Net P&L"].push(d.net_pnl ?? 0);
      vectors.Stops.push(d.entries_stopped ?? 0);
      vectors.Entries.push(d.entries_placed ?? 0);
    }

    const m: (number | null)[][] = [];
    for (const a of VARIABLES) {
      const row: (number | null)[] = [];
      for (const b of VARIABLES) {
        row.push(a === b ? 1 : pearson(vectors[a], vectors[b]));
      }
      m.push(row);
    }
    return m;
  }, [data]);

  if (data.length < 5) {
    return (
      <div>
        <h3 className="label-upper mb-2">Correlations</h3>
        <div className="bg-card rounded-lg border border-border-dim p-4 text-center text-xs text-text-dim">
          Need at least 5 trading days
        </div>
      </div>
    );
  }

  return (
    <div>
      <h3 className="label-upper mb-2">Correlations</h3>
      <div className="bg-card rounded-lg border border-border-dim p-3 overflow-x-auto">
        <table className="w-full text-[10px]">
          <thead>
            <tr>
              <th className="text-left text-text-dim py-1 pr-2" />
              {VARIABLES.map((v) => (
                <th key={v} className="text-center text-text-secondary font-normal py-1 px-1">
                  {v}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {VARIABLES.map((row, i) => (
              <tr key={row}>
                <td className="text-text-secondary font-normal py-1 pr-2 whitespace-nowrap">{row}</td>
                {VARIABLES.map((_, j) => {
                  const r = matrix[i][j];
                  return (
                    <td
                      key={j}
                      className="text-center py-1 px-1 rounded"
                      style={{
                        backgroundColor: corrBg(r),
                        color: corrColor(r),
                        fontWeight: i === j ? 400 : 600,
                      }}
                      title={`${row} vs ${VARIABLES[j]}: r = ${r === null ? "N/A" : r.toFixed(3)}`}
                    >
                      {i === j ? "--" : r === null ? "N/A" : r.toFixed(2)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
