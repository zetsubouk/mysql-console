import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    globals: true,
    include: ["tests/vitest/**/*.test.js", "tests/vitest/**/*.spec.js"],
    setupFiles: ["tests/vitest/setup.js"],
    coverage: {
      provider: "v8",
      reportsDirectory: "coverage",
      // 只统计生产前端代码,排除测试自身
      include: ["src/static/app.js", "src/static/dashboard-helpers.js"],
    },
  },
});
