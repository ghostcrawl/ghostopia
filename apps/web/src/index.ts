// ghostopia web app — public module surface (the workspace "main").
//
// The RUNTIME entry is `main.tsx` (mounted from index.html); this module re-exports the app
// shell + the Live/Simulated WS clients so the package "main" resolves to REAL code. It holds
// NO GhostCrawl SDK, NO Python package, and NO key — only the thin renderer/client surface.
export { App } from "./App";
export { startLiveClient, type LiveClientHandle, type ManageCommand } from "./liveClient";
export { SimClient } from "./simClient";
