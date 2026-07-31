// Dashboard account auth (2026-07-31 — replaces the old shared DASHBOARD_API_KEY).
//
// Real per-person login: username + password + TOTP 2FA. The backend issues
// an httpOnly session cookie on success (dashboard/backend/routers/auth.py) —
// there is no secret for this frontend to store or inject; browsers send the
// cookie automatically on same-origin requests (fetch's default credentials
// mode is "same-origin", and WebSocket always attaches matching cookies), so
// none of the app's other ~dozens of `fetch("/api/...")` call sites need any
// change.

const BASE = "/api/auth";

async function postJson(path: string, body: unknown) {
  const r = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    throw new Error(data.detail || `Request failed (${r.status})`);
  }
  return data;
}

export interface LoginStepResponse {
  step: "change_password" | "totp";
  pending_token: string;
  totp_enabled?: boolean;
}

export async function login(username: string, password: string): Promise<LoginStepResponse> {
  return postJson("/login", { username, password });
}

export async function changePassword(
  pendingToken: string,
  newPassword: string
): Promise<LoginStepResponse> {
  return postJson("/change-password", { pending_token: pendingToken, new_password: newPassword });
}

export interface SetupTotpResponse {
  pending_token: string;
  secret: string;
  qr_data_uri: string;
}

export async function setupTotp(pendingToken: string): Promise<SetupTotpResponse> {
  return postJson("/setup-totp", { pending_token: pendingToken });
}

export interface VerifyTotpResponse {
  ok: boolean;
  username: string;
  recovery_codes?: string[];
  recovery_code_used?: boolean;
}

export async function verifyTotp(pendingToken: string, code: string): Promise<VerifyTotpResponse> {
  return postJson("/verify-totp", { pending_token: pendingToken, code });
}

export async function logout(): Promise<void> {
  await fetch(`${BASE}/logout`, { method: "POST", credentials: "same-origin" }).catch(() => {});
}

/** Returns the signed-in username, or null if not authenticated. */
export async function getCurrentUser(): Promise<string | null> {
  try {
    const r = await fetch(`${BASE}/me`, { credentials: "same-origin" });
    if (!r.ok) return null;
    const data = await r.json();
    return data.username ?? null;
  } catch {
    return null;
  }
}
