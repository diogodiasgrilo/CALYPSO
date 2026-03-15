import { useEffect, useCallback } from "react";
import { X } from "lucide-react";
import { useHydraStore, type Toast } from "../../store/hydraStore";
import { colors } from "../../lib/tradingColors";

function ToastItem({ toast, removeToast }: { toast: Toast; removeToast: (id: string) => void }) {
  const dismiss = useCallback(() => removeToast(toast.id), [removeToast, toast.id]);

  useEffect(() => {
    const timer = setTimeout(dismiss, 5000);
    return () => clearTimeout(timer);
  }, [dismiss]);

  const borderColor =
    toast.type === "stop"
      ? colors.loss
      : toast.type === "entry"
        ? colors.profit
        : toast.type === "error"
          ? colors.loss
          : colors.info;

  return (
    <div
      role="alert"
      className="toast-enter flex items-start gap-2 bg-card rounded-lg p-3 shadow-lg border border-border-dim max-w-sm"
      style={{ borderLeftWidth: 3, borderLeftColor: borderColor }}
    >
      <div className="flex-1 min-w-0">
        <div className="text-xs font-semibold text-text-primary">{toast.title}</div>
        <div className="text-xs text-text-secondary mt-0.5 truncate">{toast.message}</div>
      </div>
      <button
        onClick={dismiss}
        aria-label="Dismiss notification"
        className="text-text-dim hover:text-text-primary shrink-0"
      >
        <X size={12} />
      </button>
    </div>
  );
}

export function ToastContainer() {
  const toasts = useHydraStore((s) => s.toasts);
  const removeToast = useHydraStore((s) => s.removeToast);

  if (toasts.length === 0) return null;

  return (
    <div className="fixed top-14 right-3 z-50 flex flex-col gap-2">
      {toasts.map((toast) => (
        <ToastItem
          key={toast.id}
          toast={toast}
          removeToast={removeToast}
        />
      ))}
    </div>
  );
}
