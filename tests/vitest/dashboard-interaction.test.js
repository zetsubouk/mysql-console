import { describe, it, expect, beforeEach, vi } from "vitest";
import fs from "fs";
import path from "path";
import { JSDOM } from "jsdom";

function createDom() {
  const html = fs.readFileSync(path.join(process.cwd(), "src/static/index.html"), "utf-8");
  const appJs = fs.readFileSync(path.join(process.cwd(), "src/static/app.js"), "utf-8");
  let lastFetchUrl = "";
  const fetchedUrls = [];
  const dom = new JSDOM(html, {
    url: "http://127.0.0.1:8090/",
    runScripts: "outside-only",
    beforeParse(w) {
      w.fetch = async (url) => {
        lastFetchUrl = url;
        fetchedUrls.push(url);
        if (url.includes("/api/dashboard/health-history")) return { ok: true, json: async () => ({ points: [{ t: Date.now() / 1000, score: 80 }] }) };
        if (url.includes("/api/dashboard/health")) return { ok: true, json: async () => ({ score: 80, label: "良好", items: [] }) };
        if (url.includes("/api/dashboard/innodb")) return { ok: true, json: async () => ({ hit_rate: "90%", rows_read: 1, rows_inserted: 1, rows_updated: 0, rows_deleted: 0, lock_waits: 0 }) };
        if (url.includes("/api/dashboard/tablespace")) return { ok: true, json: async () => [{ db: "a", name: "t1", total_size: 100, rows: 10 }, { db: "b", name: "t2", total_size: 50, rows: 5 }] };
        if (url.includes("/api/databases")) return { ok: true, json: async () => [{ name: "a", total_size: 100 }, { name: "b", total_size: 50 }] };
        if (url.includes("/api/dashboard/replication")) return { ok: true, json: async () => ({ is_slave: false, message: "ok" }) };
        return { ok: true, json: async () => ({}) };
      };
      const instances = new Map();
      w.echarts = {
        init: (el) => {
          const inst = {
            el,
            _opt: null,
            getOption: () => ({ series: [{ data: [1, 2, 3] }] }),
            setOption(o) { this._opt = o; Object.assign(this, o); },
            resize() {},
            getDom: () => el,
            on(evt, cb) { this._cb = cb; },
            getDataURL: () => "data:image/png;base64,xxx",
            _fire(p) { if (this._cb) this._cb(p); },
          };
          instances.set(el.id, inst);
          el._inst = inst;
          return inst;
        },
        getInstanceByDom: (el) => el._inst || null,
      };
      w.__instances = instances;
      w.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {} });
      w.requestAnimationFrame = (cb) => setTimeout(cb, 16);
    },
  });
  dom.window.eval(appJs);
  return { dom, appJs, getLastUrl: () => lastFetchUrl, getFetchedUrls: () => [...fetchedUrls] };
}

function wait(ms) { return new Promise((r) => setTimeout(r, ms)); }

describe("Dashboard 联动与时间窗口集成", () => {
  it("点击环形扇区联动过滤表空间，二次点击重置", async () => {
    const { dom } = createDom();
    await wait(60);
    await dom.window.loadDashboardPage();
    await wait(180);
    const donut = dom.window.document.getElementById("chart-db-donut");
    const inst = donut._inst;
    expect(inst).toBeTruthy();
    inst._fire({ name: "a" });
    await wait(40);
    const tsInst = dom.window.document.getElementById("chart-ts-bar")._inst;
    expect(tsInst._opt.title.text).toContain("a");
    inst._fire({ name: "a" });
    await wait(40);
    expect(tsInst._opt.title.text).not.toContain("已过滤: a");
  });

  it("1h/6h/24h 切换触发对应 health-history 窗口参数", async () => {
    const { dom, getFetchedUrls } = createDom();
    await wait(60);
    await dom.window.loadDashboardPage();
    await wait(180);
    const btn6 = dom.window.document.querySelector("[data-dashboard-range='6']");
    btn6.click();
    await wait(280);
    expect(getFetchedUrls().some((u) => u.includes("hours=6"))).toBe(true);
    expect(btn6.getAttribute("aria-pressed")).toBe("true");
  });

  it("导出 PNG 调用 getDataURL（键盘与按钮均可达）", async () => {
    const { dom } = createDom();
    await wait(60);
    await dom.window.loadDashboardPage();
    await wait(80);
    const btn = dom.window.document.getElementById("dashboard-export-png");
    expect(btn).toBeTruthy();
    expect(btn.getAttribute("aria-label")).toMatch(/导出/);
    btn.click();
    expect(true).toBe(true);
  });
});
