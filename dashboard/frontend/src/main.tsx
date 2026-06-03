import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "./index.css";
import App from "./App";
import { installApiKeyFetch } from "./apiKey";
import { LoginGate } from "./components/auth/LoginGate";

// audit M12: arm the API-key fetch shim before any request fires (no-op
// until a key is provisioned via ?api_key= / localStorage / the login gate).
installApiKeyFetch();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <LoginGate>
        <App />
      </LoginGate>
    </BrowserRouter>
  </StrictMode>
);
