import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@/utils/chartConfig";
import "@/index.css";
import App from "@/App";
import { getStoredTheme, applyTheme } from "@/lib/theme";

applyTheme(getStoredTheme());

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
