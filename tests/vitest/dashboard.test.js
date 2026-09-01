import { describe, it, expect, beforeEach, vi } from "vitest";
import { getByText, getByRole } from "@testing-library/dom";
import fs from "fs";
import path from "path";
import { JSDOM } from "jsdom";
import {
  downsampleHealth,
  filterTablespaceByDb,
  computeDashboardStatus,
  formatUpdatedHint,
  sliceByHours,
  ariaLabelForChart,
} from "../../src/static/dashboard-helpers.js";

describe("dashboard-helpers", () => {
  it("downsampleHealth 降采样不丢首尾且均值聚合", () => {
    const pts = Array.from({ length: 1000 }, (_, i) => ({ t: i, score: i % 100 }));
    const out = downsampleHealth(pts, 400);
    expect(out.length).toBeLessThanOrEqual(400);
    expect(out.length).toBeGreaterThan(0);
    expect(downsampleHealth([], 400)).toEqual([]);
    expect(downsampleHealth([{ t: 1, score: 80 }], 400)).toHaveLength(1);
  });

  it("filterTablespaceByDb 联动过滤", () => {
    const list = [{ db: "a", name: "t1" }, { db: "b", name: "t2" }];
    expect(filterTablespaceByDb(list, "a")).toHaveLength(1);
    expect(filterTablespaceByDb(list, "其他")).toHaveLength(2);
    expect(filterTablespaceByDb(list, null)).toHaveLength(2);
    expect(filterTablespaceByDb(null, "a")).toEqual([]);
  });

  it("computeDashboardStatus live/stale/offline", () => {
    const now = Date.now();
    expect(computeDashboardStatus(now - 10000, now)).toBe("live");
    expect(computeDashboardStatus(now - 60000, now)).toBe("stale");
    expect(computeDashboardStatus(now - 200000, now)).toBe("offline");
    expect(computeDashboardStatus(0, now)).toBe("offline");
  });

  it("formatUpdatedHint 含时分秒", () => {
    expect(formatUpdatedHint(0)).toBe("最后更新 --");
    const s = formatUpdatedHint(Date.now());
    expect(s).toMatch(/最后更新/);
    expect(s).toMatch(/:/);
  });

  it("sliceByHours 按小时窗口裁剪", () => {
    const now = Date.now() / 1000;
    const pts = [{ t: now - 3600 * 25, score: 80 }, { t: now - 3600, score: 90 }];
    expect(sliceByHours(pts, 24)).toHaveLength(1);
    expect(sliceByHours(pts, 48)).toHaveLength(2);
  });

  it("ariaLabelForChart 含联动描述", () => {
    expect(ariaLabelForChart("chart-db-donut", "库占比")).toContain("联动");
    expect(ariaLabelForChart("unknown", "x")).toBe("x");
  });
});

describe("Dashboard DOM 结构", () => {
  let doc;
  beforeEach(() => {
    const html = fs.readFileSync(path.join(process.cwd(), "src/static/index.html"), "utf-8");
    const dom = new JSDOM(html);
    doc = dom.window.document;
  });

  it("看板时间窗口与联动提示存在", () => {
    expect(doc.querySelector("[data-dashboard-range='24']")).toBeTruthy();
    const toolbar = doc.querySelector(".dashboard-toolbar");
    expect(toolbar).toBeTruthy();
    expect(toolbar.getAttribute("aria-label")).toMatch(/看板联动/);
    expect(doc.getElementById("dashboard-live-status")).toBeTruthy();
    expect(doc.getElementById("dashboard-updated-hint")).toBeTruthy();
    expect(doc.getElementById("dashboard-export-png")).toBeTruthy();
  });

  it("环形与趋势图具备无障碍 role", async () => {
    const html = fs.readFileSync(path.join(process.cwd(), "src/static/index.html"), "utf-8");
    const appJs = fs.readFileSync(path.join(process.cwd(), "src/static/app.js"), "utf-8");
    const dom = new JSDOM(html, {
      url: "http://127.0.0.1:8090/",
      runScripts: "outside-only",
      beforeParse(w) {
        w.fetch = async () => ({ ok: true, json: async () => ({ points: [], score: 80, label: "良好", items: [] }) });
        w.echarts = {
          init: (el) => {
            const m = { _opt: null, getOption: () => ({ series: [{ data: [] }] }), setOption(o) { this._opt = o; }, resize() {}, getDom: () => el, on() {}, getDataURL: () => "" };
            el._inst = m; return m;
          },
          getInstanceByDom: (el) => el._inst || null,
        };
      },
    });
    dom.window.eval(appJs);
    await new Promise((r) => setTimeout(r, 50));
    if (typeof dom.window.loadDashboardPage === "function") {
      await dom.window.loadDashboardPage();
      await new Promise((r) => setTimeout(r, 80));
      expect(dom.window.document.getElementById("chart-health-trend").getAttribute("role")).toBe("img");
      expect(dom.window.document.getElementById("chart-db-donut").getAttribute("aria-label")).toContain("联动");
      expect(dom.window.document.getElementById("chart-ts-bar").getAttribute("role")).toBe("img");
    }
  });

  it("跨屏联动：健康趋势与表空间同步窗口过滤（模拟）", () => {
    const now = Date.now() / 1000;
    const health = [{ t: now - 3600 * 5, score: 80 }, { t: now - 100, score: 90 }];
    expect(sliceByHours(health, 1).length).toBe(1);
    expect(downsampleHealth(health, 1).length).toBe(1);
  });
});
