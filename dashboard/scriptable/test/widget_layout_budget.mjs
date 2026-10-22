// Estimated rendered height of the Large fleet layout, in points.
//
// Measured from a real home-screen screenshot 2026-10-22: the Large tile is
// 356pt, Apple's 16pt margins leave 324pt usable, and the layout came to
// 339pt — so it OVERFLOWED and iOS compressed the padding, measuring 5.7pt at
// the top against the 16pt set. The tile looked edge-to-edge above and loose
// below. This keeps the layout inside the box as rows are added.
export const TILE = 356, MARGIN = 16, USABLE = TILE - 2 * MARGIN;

export function estimateHeight({ rows, groups, hero = true, header = true }) {
  // Line boxes, deliberately PESSIMISTIC: the model read 324pt where the real
  // screenshot measured ~318pt, so it errs toward reporting an overflow that
  // is not quite there rather than missing one that is.
  const LINE = { title: 20, sub: 14, hero: 26, colhdr: 12, grouphdr: 13, row: 15 };
  let h = LINE.title;
  if (hero) h += 5 + LINE.sub + LINE.hero;
  if (header) h += 7 + LINE.colhdr + 2;
  h += groups * (LINE.grouphdr + 1);          // group headers
  h += (groups - 1) * (4 + 1 + 4);            // separators between groups
  h += rows * LINE.row + (rows - groups) * 2; // rows + inter-row gaps
  return h;
}
