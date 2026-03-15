/** CSV export utilities. */

function escapeCSV(val: unknown): string {
  const s = String(val ?? "");
  if (s.includes(",") || s.includes('"') || s.includes("\n") || s.includes("\r")) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

function downloadCSV(filename: string, csv: string) {
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function buildCSV(data: Record<string, unknown>[]): string | null {
  if (data.length === 0) return null;
  const headers = Object.keys(data[0]);
  const headerRow = headers.map(escapeCSV).join(",");
  const rows = data.map((row) => headers.map((h) => escapeCSV(row[h])).join(","));
  return [headerRow, ...rows].join("\n");
}

/** Export daily summaries to CSV. */
export function exportDailySummariesCSV(
  data: Record<string, unknown>[]
): void {
  const csv = buildCSV(data);
  if (!csv) return;
  downloadCSV(`hydra_daily_summaries_${new Date().toISOString().slice(0, 10)}.csv`, csv);
}

/** Export entries to CSV. */
export function exportEntriesCSV(
  data: Record<string, unknown>[]
): void {
  const csv = buildCSV(data);
  if (!csv) return;
  downloadCSV(`hydra_entries_${new Date().toISOString().slice(0, 10)}.csv`, csv);
}
