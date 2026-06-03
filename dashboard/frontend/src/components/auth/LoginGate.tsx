/** Branded API-key login gate (2026-06-02).
 *
 * Wraps the whole app. On mount it validates the stored key (or one passed via
 * ?api_key=) against a guarded endpoint; if valid it renders the dashboard,
 * otherwise it shows a HYDRA-branded "enter access key" screen. When the
 * backend has no key configured (auth off), validation against the guarded
 * endpoint returns 200 with no key, so the gate transparently passes through.
 */

import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Lock, ShieldCheck, Loader2 } from "lucide-react";
import { getApiKey, setApiKey, validateApiKey } from "../../apiKey";
import { colors } from "../../lib/tradingColors";

type Status = "checking" | "needAuth" | "authed";

function stripKeyFromUrl() {
  try {
    const u = new URL(window.location.href);
    if (u.searchParams.has("api_key")) {
      u.searchParams.delete("api_key");
      window.history.replaceState({}, "", u.toString());
    }
  } catch {
    /* ignore */
  }
}

export function LoginGate({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>("checking");
  const [value, setValue] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // First load: if no key is configured on the backend, validateApiKey("")
  // would 401 — but we want auth-off deployments to pass straight through.
  // So we probe with the stored key (which may be empty); the backend returns
  // 200 for any request when no key is set, and 401 only when a key IS set and
  // ours is wrong/missing.
  useEffect(() => {
    const stored = getApiKey(); // also captures ?api_key= → localStorage
    // Probe the guarded endpoint with whatever we have (stored key, or none).
    fetch("/api/hydra/bot-config", {
      headers: stored ? { "X-API-Key": stored } : {},
    })
      .then((r) => {
        if (r.ok) {
          stripKeyFromUrl();
          setStatus("authed");
        } else {
          setStatus("needAuth");
        }
      })
      .catch(() => setStatus("needAuth"));
  }, []);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const key = value.trim();
    if (!key) return;
    setSubmitting(true);
    setError("");
    const ok = await validateApiKey(key);
    setSubmitting(false);
    if (ok) {
      setApiKey(key);
      setStatus("authed");
    } else {
      setError("Incorrect access key. Try again.");
    }
  };

  if (status === "authed") return <>{children}</>;

  if (status === "checking") {
    return (
      <div className="fixed inset-0 flex items-center justify-center bg-bg">
        <Loader2 className="animate-spin" size={28} style={{ color: colors.profit }} />
      </div>
    );
  }

  // needAuth → branded login screen
  return (
    <div
      className="fixed inset-0 flex items-center justify-center bg-bg px-4"
      style={{
        background:
          "radial-gradient(900px 500px at 50% -10%, rgba(126,232,199,0.10), transparent 60%), var(--bg, #0d1117)",
      }}
    >
      <form
        onSubmit={onSubmit}
        className="w-full max-w-sm rounded-2xl border border-border-dim p-8"
        style={{
          backgroundColor: "var(--bg-elevated, #161b22)",
          boxShadow: "0 10px 40px rgba(0,0,0,0.55)",
        }}
      >
        <div className="flex flex-col items-center text-center">
          <img
            src="/hydra-logo.png"
            alt="HYDRA"
            className="h-14 w-14 rounded-xl mb-3"
            onError={(e) => ((e.target as HTMLImageElement).style.display = "none")}
          />
          <h1 className="text-text-primary font-bold text-2xl tracking-wide">HYDRA</h1>
          <p className="text-text-secondary text-xs mt-1 flex items-center gap-1.5">
            <Lock size={12} /> Private dashboard · enter your access key
          </p>
        </div>

        <div className="mt-7">
          <input
            type="password"
            autoFocus
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="Access key"
            className="w-full rounded-lg border border-border-dim bg-bg px-3 py-2.5 text-text-primary text-sm outline-none transition-colors"
            style={{ caretColor: colors.profit }}
            onFocus={(e) => (e.currentTarget.style.borderColor = colors.profit)}
            onBlur={(e) => (e.currentTarget.style.borderColor = "")}
            autoComplete="current-password"
            spellCheck={false}
          />
          {error && (
            <div className="mt-2 text-xs font-medium" style={{ color: colors.loss }}>
              {error}
            </div>
          )}
          <button
            type="submit"
            disabled={submitting || !value.trim()}
            className="mt-4 w-full rounded-lg py-2.5 text-sm font-bold tracking-wide transition-opacity disabled:opacity-50"
            style={{ backgroundColor: colors.profit, color: "#0d1117" }}
          >
            {submitting ? (
              <span className="flex items-center justify-center gap-2">
                <Loader2 className="animate-spin" size={15} /> Checking…
              </span>
            ) : (
              <span className="flex items-center justify-center gap-2">
                <ShieldCheck size={15} /> Unlock
              </span>
            )}
          </button>
        </div>

        <p className="text-text-secondary text-[10px] text-center mt-6 leading-relaxed">
          Read-only · IBKR paper account. Your key is stored only in this browser.
        </p>
      </form>
    </div>
  );
}
