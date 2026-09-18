import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "./index.css";
import App from "./App";
import { LoginGate } from "./components/auth/LoginGate";
import { ErrorBoundary } from "./components/shared/ErrorBoundary";

// Session-cookie auth (see auth.ts) needs no request-level wiring — browsers
// send cookies automatically on same-origin fetch/WS. LoginGate below is the
// only place auth state is handled.

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      {/* Outermost catch: the per-route boundaries in App.tsx keep the shell
          navigable when ONE page throws; this one covers the shell itself —
          the layout, the header, and the login gate. */}
      <ErrorBoundary>
        <LoginGate>
          <App />
        </LoginGate>
      </ErrorBoundary>
    </BrowserRouter>
  </StrictMode>
);
