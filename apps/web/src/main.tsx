// ghostopia web — React entrypoint. Mounts the app chrome; the PixiJS render
// loop lives inside <App/> and animates outside React. No SDK/Python/key import.

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import "./styles.css";

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("ghostopia: #root element not found");

createRoot(rootEl).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
