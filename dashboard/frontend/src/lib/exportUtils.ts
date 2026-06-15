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
  // Union of keys across ALL rows (first-seen order) so a later row carrying an
  // extra column isn't silently dropped and every row stays column-aligned.
  const headers: string[] = [];
  const seen = new Set<string>();
  for (const row of data) {
    for (const k of Object.keys(row)) {
      if (!seen.has(k)) { seen.add(k); headers.push(k); }
    }
  }
  // Friendly Title-Case headers (raw keys still used to look up values).
  const humanize = (k: string) => k.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  const headerRow = headers.map((h) => escapeCSV(humanize(h))).join(",");
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
