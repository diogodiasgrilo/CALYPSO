import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "./index.css";
import App from "./App";
import { installApiKeyFetch } from "./apiKey";

// audit M12: arm the API-key fetch shim before any request fires (no-op
// until a key is provisioned via ?api_key= / localStorage).
installApiKeyFetch();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>
);
