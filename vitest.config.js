import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    globals: true,
    include: ["tests/vitest/**/*.test.js", "tests/vitest/**/*.spec.js"],
    setupFiles: ["tests/vitest/setup.js"],
    coverage: { provider: "v8", reportsDirectory: "coverage" },
  },
});
