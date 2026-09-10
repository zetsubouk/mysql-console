import { describe, it, expect, beforeEach } from "vitest";
import fs from "fs";
import path from "path";
import { JSDOM } from "jsdom";

// vitest 运行于 jsdom 环境(window 已存在):import 副作用与生产 <script> 加载等价,
// 把 DashHelpers 挂到 window。这里测的就是 index.html 实际加载的生产实现——
// 历史上本文件测的是从未被生产加载的 ESM 影子副本(app.js 里跑的是手抄份),已纠正。
import "../../src/static/dashboard-helpers.js";

describe("dashboard-helpers(生产唯一实现)", () => {
  it("downsample 降采样:不超上限、非空、单点透传、空数组安全", () => {
    const pts = Array.from({ length: 1000 }, (_, i) => ({ t: i, score: i % 100 }));
    const out = window.DashHelpers.downsample(pts, 400);
    expect(out.length).toBeLessThanOrEqual(400);
    expect(out.length).toBeGreaterThan(0);
    expect(out[0].t).toBeLessThan(out[out.length - 1].t);   // 不丢窗口边界
    expect(window.DashHelpers.downsample([], 400)).toEqual([]);
    expect(window.DashHelpers.downsample([{ t: 1, score: 80 }], 400)).toHaveLength(1);
    expect(window.DashHelpers.downsample(null, 400)).toEqual([]);
  });

  it("downsample 输出分值为桶内均值(四舍五入到 0.1),t 取桶中位点", () => {
    const pts = [{ t: 0, score: 10 }, { t: 1, score: 20 }];
    const out = window.DashHelpers.downsample(pts, 1);
    expect(out).toHaveLength(1);
    expect(out[0].score).toBe(15);
    expect(out[0].t).toBe(1); // 中位点 floor(2/2)=1(与 app.js 历史实现一致,行为不变)
  });

  it("formatUpdated 空值占位与格式", () => {
    expect(window.DashHelpers.formatUpdated(0)).toBe("最后更新 --");
    expect(window.DashHelpers.formatUpdated(null)).toBe("最后更新 --");
    const d = new Date(2026, 8, 10, 7, 8, 9); // 本地时区 2026-09-10 07:08:09
    expect(window.DashHelpers.formatUpdated(d.getTime())).toContain("最后更新 9-10 07:08:09");
  });

  it("status live/stale/offline 阈值(30s/90s)", () => {
    const now = Date.now();
    expect(window.DashHelpers.status(0, now)).toBe("offline");
    expect(window.DashHelpers.status(now - 10000, now)).toBe("live");
    expect(window.DashHelpers.status(now - 60000, now)).toBe("stale");
    expect(window.DashHelpers.status(now - 200000, now)).toBe("offline");
  });
});

describe("防漂移:app.js 必须委托 DashHelpers,不得再手抄算法", () => {
  const appSrc = fs.readFileSync(path.join(process.cwd(), "src/static/app.js"), "utf-8");

  it("app.js 薄封装指向 window.DashHelpers", () => {
    expect(appSrc).toContain("window.DashHelpers.downsample");
    expect(appSrc).toContain("window.DashHelpers.formatUpdated");
    expect(appSrc).toContain("window.DashHelpers.status(");
  });

  it("旧手抄算法体已从 app.js 删除", () => {
    expect(appSrc).not.toContain("const bucket = Math.ceil");
    expect(appSrc).not.toContain("最后更新 ${");
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
    const helpersJs = fs.readFileSync(path.join(process.cwd(), "src/static/dashboard-helpers.js"), "utf-8");
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
    // 与生产 <script> 顺序一致:helpers 先于 app.js(看板薄封装依赖 window.DashHelpers)
    dom.window.eval(helpersJs);
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
});
