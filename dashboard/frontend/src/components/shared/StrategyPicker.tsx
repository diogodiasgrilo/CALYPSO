/**
 * Main-dashboard strategy picker (Header). A grouped dropdown (optgroup per
 * group label) of every strategy whose `capabilities.main_dashboard` is true;
 * `available:false` strategies render disabled (their bot hasn't run yet).
 *
 * Selection is stored in Zustand (`selectedStrategyId`) + persisted to
 * localStorage by the store action; default = taxonomy primary.
 *
 * 100% read-only: changing the selection only changes which read source the
 * main page queries + a UI pref. No write path.
 *
 * Picker scope (audit AUD-3-F4): this is the DASHBOARD tab's control. On
 * History/Analytics (still primary-bound) it is hidden; the Header shows a
 * "History shows {primary}" note instead so the control never silently lies.
 */

import { ChevronDown } from "lucide-react";
import { useHydraStore } from "../../store/hydraStore";
import { useStrategyMeta } from "../../hooks/useStrategyMeta";
import { useSelectedStrategy } from "../../hooks/useSelectedStrategy";
import { accentForStrategy } from "../../lib/pnlShape";

export function StrategyPicker() {
  const meta = useStrategyMeta();
  const setSelectedStrategy = useHydraStore((s) => s.setSelectedStrategy);
  const { strategy } = useSelectedStrategy();

  // Nothing to pick until meta has at least one main-dashboard-capable strategy.
  if (meta.loading) return null;
  const pickable = meta.strategies.filter((s) => s.capabilities.main_dashboard);
  if (pickable.length <= 1) return null;

  // Groups in taxonomy order; only groups that actually contain a pickable
  // strategy are shown as optgroups.
  const groupsToShow = meta.groups.filter((g) =>
    pickable.some((s) => s.group_id === g.id),
  );
  // Pickable strategies that don't map to a known group (sentinel) go last.
  const ungrouped = pickable.filter(
    (s) => !meta.groupById[s.group_id] && !groupsToShow.some((g) => g.id === s.group_id),
  );

  const currentId = strategy?.id ?? meta.primaryId;
  const accent = accentForStrategy(currentId);

  return (
    <div className="relative inline-flex items-center">
      <span
        className="w-2 h-2 rounded-full mr-1.5 shrink-0"
        style={{ backgroundColor: accent }}
        aria-hidden
      />
      <div className="relative">
        <select
          value={currentId}
          onChange={(e) => setSelectedStrategy(e.target.value)}
          aria-label="Select strategy to display on the dashboard"
          title="Which strategy the main dashboard is showing"
          className="appearance-none bg-bg-elevated border border-border-dim rounded text-xs text-text-primary
                     pl-2 pr-6 py-1 cursor-pointer outline-none hover:border-border focus:border-info
                     max-w-[14rem] truncate"
        >
          {groupsToShow.map((g) => (
            <optgroup key={g.id} label={g.label}>
              {pickable
                .filter((s) => s.group_id === g.id)
                .map((s) => (
                  <option key={s.id} value={s.id} disabled={!s.available}>
                    {s.display_name}
                    {!s.available ? " (not running)" : s.is_primary ? " — primary" : ""}
                  </option>
                ))}
            </optgroup>
          ))}
          {ungrouped.length > 0 && (
            <optgroup label="Other">
              {ungrouped.map((s) => (
                <option key={s.id} value={s.id} disabled={!s.available}>
                  {s.display_name}
                  {!s.available ? " (not running)" : ""}
                </option>
              ))}
            </optgroup>
          )}
        </select>
        <ChevronDown
          size={12}
          className="pointer-events-none absolute right-1.5 top-1/2 -translate-y-1/2 text-text-dim"
        />
      </div>
    </div>
  );
}
