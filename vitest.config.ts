import { defineConfig } from "vitest/config";

// jsdom for the web app (DOM rendering), node for the pure TS packages.
export default defineConfig({
  test: {
    globals: true,
    environment: "node",
    environmentMatchGlobs: [
      ["apps/web/**", "jsdom"],
      ["packages/**", "node"],
    ],
    include: [
      "tests/unit/**/*.test.ts",
      "apps/web/**/*.test.ts",
      "apps/web/**/*.test.tsx",
      "packages/ghost-renderer/**/*.test.ts",
      "packages/ghost-art/**/*.test.ts",
    ],
    exclude: ["**/node_modules/**", "**/dist/**"],
  },
});
