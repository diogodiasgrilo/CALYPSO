/**
 * Group-scoped comparison page — replaces the single A/B/C/D Comparison tab.
 *
 * Route: /comparison/:groupId. The renderer branches by the group's
 * family / pnl_shape (from /api/strategies/groups/{groupId}/comparison's
 * envelope):
 *
 *   - ic_0dte (credit)        → the existing IC ComparisonView, fed the group's
 *                               members + baseline_id from the payload (NOT a
 *                               hardcoded "A").
 *   - calendar_multiday (debit) → CalendarComparison (debit-native panels — net
 *                               debit / open calendars / outcomes; no
 *                               credit/buffer fields).
 *
 * A single chart NEVER receives two pnl_shapes — the branch picks one renderer
 * per group, and a payload whose shape doesn't match its renderer falls into an
 * error card (audit AUD-3-F5).
 */

import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { useStrategyMeta } from "../hooks/useStrategyMeta";
import { ComparisonView } from "./Comparison";
import type { ComparisonPayload, AggregatePayload } from "./Comparison";
import { CalendarComparison } from "../components/comparison/CalendarComparison";
import type {
  CalendarComparisonPayload,
  CalendarAggregatePayload,
} from "../components/comparison/CalendarComparison";

const POLL_MS = 2000;
const AGG_POLL_MS = 30_000;

// The group comparison envelope (one family/pnl_shape per group + baseline_id).
interface GroupComparisonEnvelope {
  group_id: string;
  label: string;
  family: string;
  pnl_shape: "credit" | "debit" | "unknown";
  comparable: boolean;
  baseline_id: string | null;
  member_ids: string[];
  date?: string;
  // ic_0dte
  leaderboard?: ComparisonPayload["leaderboard"];
  variants?: ComparisonPayload["variants"];
  // calendar_multiday
  calendars?: CalendarComparisonPayload["calendars"];
}

interface GroupAggregateEnvelope {
  group_id: string;
  family: string;
  pnl_shape: string;
  member_ids: string[];
  // ic_0dte
  variants?: AggregatePayload["variants"];
  head_to_head?: AggregatePayload["head_to_head"];
  // calendar_multiday
  calendars?: CalendarAggregatePayload;
}

async function fetchJSON<T>(url: string): Promise<T | null> {
  try {
    const r = await fetch(url);
    if (!r.ok) return null;
    return (await r.json()) as T;
  } catch {
    return null;
  }
}

function ErrorCard({ title, message }: { title: string; message: string }) {
  return (
    <div className="p-6 text-text-secondary">
      <h1 className="text-xl text-text-primary mb-2">{title}</h1>
      <div className="rounded border border-border-dim bg-card p-4 text-sm">{message}</div>
    </div>
  );
}

export function GroupComparison() {
  const { groupId = "" } = useParams<{ groupId: string }>();
  const meta = useStrategyMeta();
  const [comparison, setComparison] = useState<GroupComparisonEnvelope | null>(null);
  const [aggregate, setAggregate] = useState<GroupAggregateEnvelope | null>(null);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    let mounted = true;
    setComparison(null);
    setAggregate(null);
    setNotFound(false);

    const tick = async () => {
      const json = await fetchJSON<GroupComparisonEnvelope>(
        `/api/strategies/groups/${groupId}/comparison`,
      );
      if (!mounted) return;
      if (!json) {
        setNotFound(true);
        return;
      }
      setNotFound(false);
      setComparison(json);
    };
    const aggTick = async () => {
      const json = await fetchJSON<GroupAggregateEnvelope>(
        `/api/strategies/groups/${groupId}/aggregate`,
      );
      if (mounted && json) setAggregate(json);
    };

    tick();
    aggTick();
    const id = setInterval(tick, POLL_MS);
    const aggId = setInterval(aggTick, AGG_POLL_MS);
    return () => {
      mounted = false;
      clearInterval(id);
      clearInterval(aggId);
    };
  }, [groupId]);

  const groupMeta = meta.groupById[groupId];

  if (!meta.loading && !groupMeta) {
    return <ErrorCard title="Unknown group" message={`No comparison group "${groupId}" exists.`} />;
  }

  if (notFound) {
    return (
      <ErrorCard
        title={groupMeta?.label ?? "Comparison"}
        message="This comparison group is unavailable. The backend may not be running its variants."
      />
    );
  }

  if (!comparison) {
    return (
      <div className="p-6 text-text-secondary">
        <h1 className="text-xl text-text-primary mb-2">{groupMeta?.label ?? "Comparison"}</h1>
        <p className="text-sm">Loading group data…</p>
      </div>
    );
  }

  // ── CREDIT (iron condor) ──
  if (comparison.pnl_shape === "credit") {
    if (!comparison.variants || !comparison.leaderboard) {
      return (
        <ErrorCard
          title={comparison.label}
          message="This credit group's payload is missing its variants/leaderboard. (Shape mismatch — refusing to render.)"
        />
      );
    }
    // Adapt the group envelope into the IC ComparisonView's expected shape.
    const data: ComparisonPayload = {
      date: comparison.date ?? "",
      leaderboard: comparison.leaderboard,
      variants: comparison.variants,
    };
    const agg: AggregatePayload | null =
      aggregate && aggregate.variants && aggregate.head_to_head
        ? { variants: aggregate.variants, head_to_head: aggregate.head_to_head }
        : null;
    // Members come from the payload (uppercased letters), NOT hardcoded A/B/C.
    const variantIds = comparison.member_ids.map((id) => id.toUpperCase());
    return <ComparisonView data={data} aggregate={agg} variantIds={variantIds} />;
  }

  // ── DEBIT (double calendar) ──
  if (comparison.pnl_shape === "debit") {
    if (!comparison.calendars) {
      return (
        <ErrorCard
          title={comparison.label}
          message="This debit group's payload is missing its calendar members. (Shape mismatch — refusing to render.)"
        />
      );
    }
    const data: CalendarComparisonPayload = {
      group_id: comparison.group_id,
      label: comparison.label,
      family: comparison.family,
      pnl_shape: comparison.pnl_shape,
      baseline_id: comparison.baseline_id,
      member_ids: comparison.member_ids,
      calendars: comparison.calendars,
    };
    const agg: CalendarAggregatePayload | null =
      aggregate && aggregate.calendars ? aggregate.calendars : null;
    return <CalendarComparison data={data} aggregate={agg} />;
  }

  return (
    <ErrorCard
      title={comparison.label}
      message={`This group has an unsupported P&L shape ("${comparison.pnl_shape}") and cannot be compared.`}
    />
  );
}
