/** Skeleton loading placeholder with shimmer animation. */

interface SkeletonProps {
  variant?: "text" | "card" | "chart" | "metric";
  height?: number;
  className?: string;
}

export function Skeleton({ variant = "text", height, className = "" }: SkeletonProps) {
  const baseClass = "skeleton";

  switch (variant) {
    case "card":
      return (
        <div role="status" aria-label="Loading" className={`${baseClass} bg-card rounded-lg ${className}`} style={{ height: height ?? 180 }} />
      );
    case "chart":
      return (
        <div role="status" aria-label="Loading chart" className={`${baseClass} bg-card rounded-lg ${className}`} style={{ height: height ?? 300 }} />
      );
    case "metric":
      // ⚠️ This used to be two loose bars with NO CARD, while the real metric
      // is a bordered, padded, full-width card holding a label and a large
      // value. On a phone, where the grid is a single column, the placeholder
      // read as a half-built layout rather than as "this card is loading" —
      // the loading state did not resemble the thing it stood in for.
      // The wrapper now matches `bg-card rounded-lg border border-border-dim
      // p-4`, the same shell the loaded card and both message states use, so
      // the page does not reflow when data arrives.
      return (
        <div
          role="status"
          aria-label="Loading metric"
          className={`bg-card rounded-lg border border-border-dim p-4 flex flex-col gap-2 ${className}`}
        >
          <div className={`${baseClass} h-3 w-24 rounded`} />
          <div className={`${baseClass} h-8 w-32 rounded`} />
        </div>
      );
    default:
      return (
        <div role="status" aria-label="Loading" className={`${baseClass} h-4 rounded ${className}`} style={{ height }} />
      );
  }
}
