import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "./index.css";
import App from "./App";
import { LoginGate } from "./components/auth/LoginGate";

// Session-cookie auth (see auth.ts) needs no request-level wiring — browsers
// send cookies automatically on same-origin fetch/WS. LoginGate below is the
// only place auth state is handled.

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <LoginGate>
        <App />
      </LoginGate>
    </BrowserRouter>
  </StrictMode>
);
