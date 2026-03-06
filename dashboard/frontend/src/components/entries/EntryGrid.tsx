import { useHydraStore } from "../../store/hydraStore";
import { EntryCard } from "./EntryCard";

export function EntryGrid() {
  const { hydraState } = useHydraStore();
  const entries = hydraState?.entries ?? [];

  // Pad to 5 slots
  const slots = Array.from({ length: 5 }, (_, i) =>
    entries.find((e) => e.entry_number === i + 1) ?? null
  );

  return (
    <div>
      <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-2">
        Entries
      </h3>
      <div className="grid grid-cols-5 gap-2 max-lg:grid-cols-3 max-sm:grid-cols-1">
        {slots.map((entry, i) =>
          entry ? (
            <EntryCard key={i} entry={entry} />
          ) : (
            <div
              key={i}
              className="bg-card rounded-lg border border-border-dim p-3 flex items-center justify-center min-h-[120px]"
            >
              <span className="text-text-dim text-xs">E{i + 1}</span>
            </div>
          )
        )}
      </div>
    </div>
  );
}
