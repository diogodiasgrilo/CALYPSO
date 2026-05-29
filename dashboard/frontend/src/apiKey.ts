// Dashboard API-key handling (audit M12).
//
// The backend requires `X-API-Key` on /api/* (and ?api_key= on /ws/) only
// when DASHBOARD_API_KEY is configured. The dozens of existing
// `fetch("/api/...")` calls don't each carry the header, so this installs a
// single global fetch shim that injects it for same-origin /api/ and /ws/
// requests — keeping the UI working once auth is armed without editing every
// call site. It is a NO-OP when no key is present, so it is safe to ship
// before auth is turned on.
//
// Provision the key once via `?api_key=<KEY>` (persisted to localStorage,
// the same store useWebSocket.ts already reads). Get the value from:
//   gcloud secrets versions access latest --secret=calypso-dashboard-api-key \
//     --project=calypso-trading-bot

const STORAGE_KEY = "calypso-api-key";

export function getApiKey(): string {
  try {
    const fromUrl = new URLSearchParams(window.location.search).get("api_key");
    if (fromUrl) localStorage.setItem(STORAGE_KEY, fromUrl);
    return localStorage.getItem(STORAGE_KEY) || "";
  } catch {
    return "";
  }
}

export function installApiKeyFetch(): void {
  const key = getApiKey();
  if (!key) return; // auth not in use — leave fetch untouched
  const orig = window.fetch.bind(window);
  window.fetch = (input: RequestInfo | URL, init: RequestInit = {}) => {
    try {
      const url =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.pathname
            : (input as Request).url;
      if (url && (url.includes("/api/") || url.includes("/ws/"))) {
        const headers = new Headers(init.headers);
        if (!headers.has("X-API-Key")) headers.set("X-API-Key", key);
        init = { ...init, headers };
      }
    } catch {
      /* fall through to the unmodified request */
    }
    return orig(input as RequestInfo, init);
  };
}
