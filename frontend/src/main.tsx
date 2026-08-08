import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { IconSprite } from "./components/Icon";
import { applyStoredTheme } from "./theme";
import "./styles/theme.css";
import "./styles/layout.css";

applyStoredTheme();

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error("Root element not found");
}

const queryClient = new QueryClient();

createRoot(rootElement).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      {/* Above <App/> (and so outside <AuthGate/>): a <use> can only resolve a symbol that's
          already in the document, and mounting here means the login screen gets icons too. */}
      <IconSprite />
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
