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
      return (
        <div role="status" aria-label="Loading metric" className={`flex flex-col gap-2 ${className}`}>
          <div className={`${baseClass} h-3 w-16 rounded`} />
          <div className={`${baseClass} h-8 w-24 rounded`} />
        </div>
      );
    default:
      return (
        <div role="status" aria-label="Loading" className={`${baseClass} h-4 rounded ${className}`} style={{ height }} />
      );
  }
}
