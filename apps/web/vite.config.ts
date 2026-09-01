// ghostopia web — Vite config. React + a workspace that reaches the shared
// assets/ + maps/ DATA (bundled via `new URL(..., import.meta.url)`), served/
// built as a thin PixiJS renderer. No SDK/Python/key ever enters this bundle.

import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const here = dirname(fileURLToPath(import.meta.url));
const ghostopiaRoot = resolve(here, "../..");

export default defineConfig({
  plugins: [react()],
  server: {
    fs: {
      // allow importing the shared assets/ + maps/ DATA above the app root
      allow: [ghostopiaRoot],
    },
  },
  build: {
    target: "es2022",
    sourcemap: true,
  },
});
