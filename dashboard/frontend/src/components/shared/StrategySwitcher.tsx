/** Strategy switcher — replaces the native `<select>` picker (2026-09-18).
 *
 * WHY. The picker decided what every number on the page MEANT, and it was the
 * plainest control available: a 224px `<select>` that collapsed to
 * "Brandon Na…" on a 390px phone — the single most important piece of state,
 * truncated to ambiguity. A dropdown also HIDES the fleet: seven strategies
 * run, and the UI showed one name and made you open a menu to remember the
 * others existed.
 *
 * THE LETTER LEADS. This whole project thinks in letters — "B is the live
 * seat", "variant F", "D and E are calendars" — every doc, every commit, every
 * conversation. The letters lived in `short_name` and the UI never showed them.
 * A letter badge is the shortest unambiguous identity there is, and it survives
 * truncation: "[B] Brandon Na…" is still identifiable where "Brandon Na…" is
 * not.
 *
 * NAME, THEN SPEC. The seven names carried four different conventions, and B
 * and C differed only by a parenthetical. `subtitle` (taxonomy, 2026-09-18) is
 * the "14-inch, M3" line under the product name. `display_name` was left alone
 * deliberately: it is the ALERT IDENTITY on the live seat, so renaming it would
 * change what arrives in Telegram for the only variant placing real orders.
 *
 * LIVE IS A BADGE. It used to be the string " — LIVE" appended to the label,
 * doing a badge's job with text, which left six of seven looking identical in
 * kind.
 */

import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown } from "lucide-react";
import { useHydraStore } from "../../store/hydraStore";
import { useStrategyMeta } from "../../hooks/useStrategyMeta";
import { useSelectedStrategy } from "../../hooks/useSelectedStrategy";
import { accentForStrategy } from "../../lib/pnlShape";
import { colors } from "../../lib/tradingColors";

function LetterBadge({ id, size = 20 }: { id: string; size?: number }) {
  const accent = accentForStrategy(id);
  return (
    <span
      className="inline-flex items-center justify-center rounded font-bold shrink-0"
      style={{
        width: size,
        height: size,
        // Mixed toward black rather than an alpha tint, for the same reason the
        // status badges are: an alpha self-tint lifts the background toward the
        // text and costs contrast on exactly the colours that can least afford
        // it. See StatusBadge.pillBackground.
        backgroundColor: mixToBlack(accent, 0.22),
        color: accent,
        fontSize: size <= 20 ? 11 : 13,
        lineHeight: 1,
      }}
      aria-hidden
    >
      {id.toUpperCase()}
    </span>
  );
}

function mixToBlack(hex: string, mix: number): string {
  const h = hex.replace("#", "");
  const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  const n = parseInt(full, 16);
  if (Number.isNaN(n)) return "rgb(24, 28, 33)";
  const m = (v: number) => Math.round(v * mix);
  return `rgb(${m((n >> 16) & 255)}, ${m((n >> 8) & 255)}, ${m(n & 255)})`;
}

function LiveBadge() {
  return (
    <span
      className="text-3xs font-bold uppercase tracking-wider px-1.5 py-0.5 rounded shrink-0"
      style={{ backgroundColor: mixToBlack(colors.profit, 0.22), color: colors.profit }}
    >
      Live
    </span>
  );
}

export function StrategySwitcher() {
  const meta = useStrategyMeta();
  const setSelectedStrategy = useHydraStore((s) => s.setSelectedStrategy);
  const { strategy } = useSelectedStrategy();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  // Close on outside click / Escape. Registered unconditionally so the hook
  // order cannot change between renders (the early returns below are AFTER it).
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (meta.loading) return null;
  const pickable = meta.strategies.filter((s) => s.capabilities.main_dashboard);
  if (pickable.length <= 1) return null;

  const currentId = strategy?.id ?? meta.primaryId;
  const current = pickable.find((s) => s.id === currentId);
  const groupsToShow = meta.groups.filter((g) => pickable.some((s) => s.group_id === g.id));

  return (
    <div className="relative min-w-0" ref={rootRef}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label="Switch strategy"
        className="min-h-11 flex items-center gap-2 min-w-0 rounded-lg border border-border-dim
                   bg-bg-elevated pl-1.5 pr-2 py-1 transition-colors hover:border-border
                   focus-visible:border-info focus-visible:outline-none"
      >
        <LetterBadge id={currentId} />
        {/* The NAME is hidden on a phone, deliberately. Showing it in full
            consumed the header and pushed the SPX price off the screen
            entirely — which is a worse trade than the truncation it fixed.
            The letter badge IS the unambiguous identity (that is the whole
            argument for leading with it), so [B] + LIVE is enough at 390px
            and the popover carries every detail. */}
        <span className="max-sm:hidden text-xs font-semibold text-text-primary truncate min-w-0">
          {current?.display_name ?? currentId.toUpperCase()}
        </span>
        {current?.is_live && <LiveBadge />}
        <ChevronDown size={12} className="text-text-dim shrink-0" />
      </button>

      {open && (
        <div
          role="listbox"
          aria-label="Strategies"
          className="absolute left-0 top-full mt-1.5 z-50 w-[22rem] max-sm:w-[calc(100vw-2rem)]
                     rounded-xl border border-border-dim overflow-hidden"
          style={{ backgroundColor: "var(--color-bg-elevated)", boxShadow: "0 12px 40px rgba(0,0,0,0.55)" }}
        >
          {groupsToShow.map((g) => (
            <div key={g.id}>
              <div className="px-3 pt-2.5 pb-1 text-3xs font-bold uppercase tracking-wider text-text-dim">
                {g.label}
              </div>
              {pickable
                .filter((s) => s.group_id === g.id)
                .map((s) => {
                  const selected = s.id === currentId;
                  return (
                    <button
                      key={s.id}
                      type="button"
                      role="option"
                      aria-selected={selected}
                      disabled={!s.available}
                      onClick={() => {
                        setSelectedStrategy(s.id);
                        setOpen(false);
                      }}
                      className="w-full min-h-11 flex items-start gap-2.5 px-3 py-2 text-left
                                 transition-colors hover:bg-card disabled:opacity-40
                                 disabled:cursor-not-allowed focus-visible:bg-card
                                 focus-visible:outline-none"
                    >
                      <span className="pt-0.5">
                        <LetterBadge id={s.id} size={22} />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-1.5">
                          <span className="text-xs font-semibold text-text-primary truncate">
                            {s.display_name}
                          </span>
                          {s.is_live && <LiveBadge />}
                          {!s.available && (
                            <span className="text-3xs text-text-dim shrink-0">not running</span>
                          )}
                        </span>
                        {s.subtitle && (
                          <span className="block text-2xs text-text-secondary leading-snug mt-0.5">
                            {s.subtitle}
                          </span>
                        )}
                      </span>
                      {selected && (
                        <Check size={14} className="text-text-secondary shrink-0 mt-1" />
                      )}
                    </button>
                  );
                })}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
