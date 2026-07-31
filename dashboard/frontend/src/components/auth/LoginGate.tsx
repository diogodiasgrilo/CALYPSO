/** Branded multi-user login gate (2026-07-31 — real accounts + TOTP 2FA).
 *
 * Replaces the old single-shared-access-key gate. Wraps the whole app. On
 * mount it checks for a valid session (GET /api/auth/me); if authenticated
 * it renders the dashboard, otherwise it walks the visitor through:
 *   1. username + password
 *   2. forced password change (first login on an admin-created account)
 *   3. TOTP 2FA — scan a QR + enter a code (first time), or just enter a
 *      code / recovery code (subsequent logins)
 *   4. one-time recovery-code display (first-time 2FA enrollment only)
 *
 * When the backend has zero accounts configured (dev-mode, see
 * dashboard/backend/auth.py:dev_mode_open), /api/auth/me returns 200 with a
 * placeholder username, so the gate transparently passes through.
 */

import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Lock, ShieldCheck, Loader2, KeyRound, Copy, Check } from "lucide-react";
import * as auth from "../../auth";
import { colors } from "../../lib/tradingColors";

type Step =
  | "checking"
  | "login"
  | "changePassword"
  | "totp"
  | "recoveryCodes"
  | "authed";

export function LoginGate({ children }: { children: ReactNode }) {
  const [step, setStep] = useState<Step>("checking");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // Login form
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  // Carried across steps
  const [pendingToken, setPendingToken] = useState("");
  const [totpEnabled, setTotpEnabled] = useState(false);
  const [qrDataUri, setQrDataUri] = useState("");
  const [totpSecret, setTotpSecret] = useState("");
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);

  // Step-local form fields
  const [newPassword, setNewPassword] = useState("");
  const [newPassword2, setNewPassword2] = useState("");
  const [code, setCode] = useState("");
  const [codesCopied, setCodesCopied] = useState(false);

  useEffect(() => {
    auth.getCurrentUser().then((u) => setStep(u ? "authed" : "login"));
  }, []);

  const resetError = () => setError("");

  const handleLogin = async (e: FormEvent) => {
    e.preventDefault();
    resetError();
    setSubmitting(true);
    try {
      const res = await auth.login(username.trim(), password);
      setPassword("");
      setPendingToken(res.pending_token);
      if (res.step === "change_password") {
        setStep("changePassword");
      } else {
        setTotpEnabled(!!res.totp_enabled);
        if (!res.totp_enabled) {
          const setup = await auth.setupTotp(res.pending_token);
          setQrDataUri(setup.qr_data_uri);
          setTotpSecret(setup.secret);
        }
        setStep("totp");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed.");
    } finally {
      setSubmitting(false);
    }
  };

  const handleChangePassword = async (e: FormEvent) => {
    e.preventDefault();
    resetError();
    if (newPassword !== newPassword2) {
      setError("Passwords don't match.");
      return;
    }
    setSubmitting(true);
    try {
      const res = await auth.changePassword(pendingToken, newPassword);
      setNewPassword("");
      setNewPassword2("");
      setTotpEnabled(!!res.totp_enabled);
      if (!res.totp_enabled) {
        const setup = await auth.setupTotp(res.pending_token);
        setQrDataUri(setup.qr_data_uri);
        setTotpSecret(setup.secret);
      }
      setStep("totp");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't set new password.");
    } finally {
      setSubmitting(false);
    }
  };

  const handleVerifyTotp = async (e: FormEvent) => {
    e.preventDefault();
    resetError();
    setSubmitting(true);
    try {
      const res = await auth.verifyTotp(pendingToken, code.trim());
      setCode("");
      if (res.recovery_codes && res.recovery_codes.length > 0) {
        setRecoveryCodes(res.recovery_codes);
        setStep("recoveryCodes");
      } else {
        setStep("authed");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Invalid code.");
    } finally {
      setSubmitting(false);
    }
  };

  const copyRecoveryCodes = () => {
    navigator.clipboard?.writeText(recoveryCodes.join("\n")).then(() => {
      setCodesCopied(true);
      setTimeout(() => setCodesCopied(false), 2000);
    });
  };

  if (step === "authed") return <>{children}</>;

  if (step === "checking") {
    return (
      <div className="fixed inset-0 flex items-center justify-center bg-bg">
        <Loader2 className="animate-spin" size={28} style={{ color: colors.profit }} />
      </div>
    );
  }

  return (
    <div
      className="fixed inset-0 flex items-center justify-center bg-bg px-4"
      style={{
        background:
          "radial-gradient(900px 500px at 50% -10%, rgba(126,232,199,0.10), transparent 60%), var(--bg, #0d1117)",
      }}
    >
      <div
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
            <Lock size={12} />
            {step === "login" && "Sign in to your account"}
            {step === "changePassword" && "Set a new password"}
            {step === "totp" && (totpEnabled ? "Enter your 2FA code" : "Set up two-factor auth")}
            {step === "recoveryCodes" && "Save your recovery codes"}
          </p>
        </div>

        {step === "login" && (
          <form onSubmit={handleLogin} className="mt-7 space-y-3">
            <input
              type="text"
              autoFocus
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="Username"
              className={inputClass}
              style={inputStyle}
              autoComplete="username"
            />
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Password"
              className={inputClass}
              style={inputStyle}
              autoComplete="current-password"
            />
            <ErrorLine error={error} />
            <SubmitButton
              submitting={submitting}
              disabled={!username.trim() || !password}
              label="Sign in"
              icon={<ShieldCheck size={15} />}
            />
          </form>
        )}

        {step === "changePassword" && (
          <form onSubmit={handleChangePassword} className="mt-7 space-y-3">
            <p className="text-text-secondary text-xs -mt-1 mb-1">
              First login — choose a new password (min. 12 characters).
            </p>
            <input
              type="password"
              autoFocus
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              placeholder="New password"
              className={inputClass}
              style={inputStyle}
              autoComplete="new-password"
            />
            <input
              type="password"
              value={newPassword2}
              onChange={(e) => setNewPassword2(e.target.value)}
              placeholder="Confirm new password"
              className={inputClass}
              style={inputStyle}
              autoComplete="new-password"
            />
            <ErrorLine error={error} />
            <SubmitButton
              submitting={submitting}
              disabled={newPassword.length < 12 || !newPassword2}
              label="Continue"
              icon={<ShieldCheck size={15} />}
            />
          </form>
        )}

        {step === "totp" && (
          <form onSubmit={handleVerifyTotp} className="mt-7 space-y-3">
            {!totpEnabled && qrDataUri && (
              <div className="flex flex-col items-center gap-2 mb-2">
                <p className="text-text-secondary text-xs text-center">
                  Scan with an authenticator app (Google Authenticator, Authy, 1Password…)
                </p>
                <img src={qrDataUri} alt="2FA QR code" className="rounded-lg" width={160} height={160} />
                <p className="text-text-secondary text-[10px] text-center break-all">
                  Or enter manually: <span className="font-mono">{totpSecret}</span>
                </p>
              </div>
            )}
            <input
              type="text"
              inputMode="numeric"
              autoFocus
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder={totpEnabled ? "6-digit code or recovery code" : "6-digit code"}
              className={inputClass}
              style={inputStyle}
              autoComplete="one-time-code"
            />
            <ErrorLine error={error} />
            <SubmitButton
              submitting={submitting}
              disabled={!code.trim()}
              label="Verify"
              icon={<KeyRound size={15} />}
            />
          </form>
        )}

        {step === "recoveryCodes" && (
          <div className="mt-7 space-y-3">
            <p className="text-text-secondary text-xs">
              Save these — each works once if you lose your authenticator. They won't be shown again.
            </p>
            <div
              className="rounded-lg border border-border-dim p-3 font-mono text-xs grid grid-cols-2 gap-1.5"
              style={{ backgroundColor: "var(--bg, #0d1117)" }}
            >
              {recoveryCodes.map((c) => (
                <div key={c} className="text-text-primary">
                  {c}
                </div>
              ))}
            </div>
            <button
              type="button"
              onClick={copyRecoveryCodes}
              className="w-full rounded-lg border border-border-dim py-2 text-xs font-medium text-text-secondary flex items-center justify-center gap-2 transition-colors hover:text-text-primary"
            >
              {codesCopied ? <Check size={13} /> : <Copy size={13} />}
              {codesCopied ? "Copied" : "Copy to clipboard"}
            </button>
            <button
              type="button"
              onClick={() => setStep("authed")}
              className="w-full rounded-lg py-2.5 text-sm font-bold tracking-wide"
              style={{ backgroundColor: colors.profit, color: "#0d1117" }}
            >
              I've saved these — continue
            </button>
          </div>
        )}

        <p className="text-text-secondary text-[10px] text-center mt-6 leading-relaxed">
          Read-only · IBKR paper account.
        </p>
      </div>
    </div>
  );
}

const inputClass =
  "w-full rounded-lg border border-border-dim bg-bg px-3 py-2.5 text-text-primary text-sm outline-none transition-colors";
const inputStyle = { caretColor: colors.profit };

function ErrorLine({ error }: { error: string }) {
  if (!error) return null;
  return (
    <div className="text-xs font-medium" style={{ color: colors.loss }}>
      {error}
    </div>
  );
}

function SubmitButton({
  submitting,
  disabled,
  label,
  icon,
}: {
  submitting: boolean;
  disabled: boolean;
  label: string;
  icon: ReactNode;
}) {
  return (
    <button
      type="submit"
      disabled={submitting || disabled}
      className="w-full rounded-lg py-2.5 text-sm font-bold tracking-wide transition-opacity disabled:opacity-50"
      style={{ backgroundColor: colors.profit, color: "#0d1117" }}
    >
      {submitting ? (
        <span className="flex items-center justify-center gap-2">
          <Loader2 className="animate-spin" size={15} /> Working…
        </span>
      ) : (
        <span className="flex items-center justify-center gap-2">
          {icon} {label}
        </span>
      )}
    </button>
  );
}
