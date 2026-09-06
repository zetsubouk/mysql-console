import { describe, it, expect } from "vitest";
import fs from "fs";
import path from "path";
import { JSDOM } from "jsdom";

// 向导第 1 步(本机数据库检测/安装引导)的 jsdom 集成测试。
// 模式同 dashboard-interaction.test.js: 加载真实 index.html + app.js,mock fetch 路由。

function wait(ms) { return new Promise((r) => setTimeout(r, ms)); }

function createDom(routes) {
  const html = fs.readFileSync(path.join(process.cwd(), "src/static/index.html"), "utf-8");
  const appJs = fs.readFileSync(path.join(process.cwd(), "src/static/app.js"), "utf-8");
  const calls = [];
  const dom = new JSDOM(html, {
    url: "http://127.0.0.1:8090/",
    runScripts: "outside-only",
    beforeParse(w) {
      w.fetch = async (url, opt = {}) => {
        const method = (opt.method || "GET").toUpperCase();
        let body = null;
        try { body = opt.body ? JSON.parse(opt.body) : null; } catch (e) { /* ignore */ }
        calls.push({ method, url, body });
        for (const r of routes) {
          if (url.includes(r.url) && (r.method || "GET") === method) {
            const data = typeof r.data === "function" ? r.data(body, calls.length) : r.data;
            return { ok: true, status: 200, json: async () => data };
          }
        }
        return { ok: true, status: 200, json: async () => ({}) };
      };
      w.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {} });
      w.requestAnimationFrame = (cb) => setTimeout(cb, 16);
    },
  });
  dom.window.eval(appJs);
  return { dom, calls };
}

const BASE = [
  { url: "/api/settings", data: { setup_done: false } },
  { url: "/api/setup/env", data: { items: [], mysql_bin_found: "", bundled_tools: [] } },
  { url: "/api/version", data: { version: "3.8.1", platform: "linux" } },
];

function dbRoutes(over = []) {
  // 覆盖路由放最前: mock 按数组顺序取首个匹配
  return [
    ...over,
    ...BASE,
    {
      url: "/api/setup/db-detect",
      data: { installed: false, summary: "未检测到本机 MySQL/MariaDB 数据库", port: 3306, port_open: false },
    },
    {
      url: "/api/setup/mysql-versions",
      data: {
        source: "builtin",
        versions: [
          { version: "9.7.2", lts: true, win_url: "https://x/9.zip", linux_url: "https://x/9.tar.xz" },
          { version: "8.0.45", lts: false, win_url: "https://x/8.zip", linux_url: "https://x/8.tar.xz" },
        ],
      },
    },
    {
      url: "/api/setup/mysql-suggestions", method: "POST",
      data: {
        ok: true, version: [9, 7, 2], errors: [], warnings: [],
        suggestions: { innodb_buffer_pool_size: "512M", max_connections: 300, port: 3306, collation_server: "utf8mb4_0900_ai_ci" },
        my_cnf_preview: "[mysqld]\nbasedir=/opt/mysql\n",
      },
    },
  ];
}

describe("向导第 1 步:本机数据库检测与安装引导", () => {
  it("未检测到数据库时展示二选一入口", async () => {
    const { dom } = createDom(dbRoutes());
    await wait(60);
    await dom.window.openSetup(true);
    await wait(150);
    const d = dom.window.document;
    expect(d.getElementById("setup-modal").classList.contains("hidden")).toBe(false);
    expect(d.getElementById("su-pane-db").classList.contains("hidden")).toBe(false);
    expect(d.getElementById("su-db-choose").classList.contains("hidden")).toBe(false);
    expect(d.getElementById("su-btn-install-db").textContent).toContain("安装本机数据库");
    expect(d.getElementById("su-btn-remote-db").textContent).toContain("远程");
  });

  it("检测到数据库时展示结论且不显示选择按钮", async () => {
    const routes = dbRoutes([
      { url: "/api/setup/db-detect",
        data: { installed: true, summary: "检测到本机数据库: MySQL80(running)", service_name: "MySQL80" } },
    ]);
    const { dom } = createDom(routes);
    await wait(60);
    await dom.window.openSetup(true);
    await wait(150);
    const d = dom.window.document;
    expect(d.getElementById("su-db-detect").textContent).toContain("MySQL80");
    expect(d.getElementById("su-db-choose").classList.contains("hidden")).toBe(true);
  });

  it("点「安装本机数据库」加载版本与建议值,datadir 自动补默认值", async () => {
    const { dom } = createDom(dbRoutes());
    await wait(60);
    await dom.window.openSetup(true);
    await wait(150);
    const d = dom.window.document;
    d.getElementById("su-btn-install-db").click();
    await wait(200);
    // 版本下拉(LTS 标记)
    expect(d.getElementById("su-db-version").options.length).toBe(2);
    expect(d.getElementById("su-db-version").options[0].textContent).toContain("LTS");
    expect(d.getElementById("su-db-version-src").textContent).toContain("内置清单");
    // 建议值预填
    expect(d.getElementById("su-db-pool").value).toBe("512M");
    expect(d.getElementById("su-db-conn").value).toBe("300");
    // datadir 默认值联动(需求 1.2)
    const basedir = d.getElementById("su-db-basedir");
    basedir.value = "/opt/mysql";
    basedir.dispatchEvent(new dom.window.Event("change"));
    expect(d.getElementById("su-db-datadir").value).toBe("/opt/mysql/data");
    // my.ini 预览已展示
    expect(d.getElementById("su-db-preview").textContent).toContain("[mysqld]");
  });

  it("点「连接远程」进入环境检测步(分支2串联)", async () => {
    const { dom } = createDom(dbRoutes());
    await wait(60);
    await dom.window.openSetup(true);
    await wait(150);
    const d = dom.window.document;
    d.getElementById("su-btn-remote-db").click();
    await wait(80);
    expect(d.getElementById("su-pane-1").classList.contains("hidden")).toBe(false);
    expect(d.getElementById("su-pane-db").classList.contains("hidden")).toBe(true);
    // 步骤条推进:第 2 步高亮
    expect(d.querySelector('.s-step[data-step="2"]').classList.contains("active")).toBe(true);
  });

  it("开始安装→轮询完成→连接表单自动回填", async () => {
    let posted = null;
    const routes = dbRoutes([
      { url: "/api/setup/install-mysql", method: "POST",
        data: (body) => { posted = body; return { ok: true, status: "running" }; } },
      { url: "/api/setup/install-mysql/status",
        data: { status: "done", percent: 100, phase: "done", msg: "MySQL 8.0.45 安装完成",
                warnings: [], conn: { host: "127.0.0.1", port: 3307 } } },
    ]);
    const { dom, calls } = createDom(routes);
    await wait(60);
    await dom.window.openSetup(true);
    await wait(150);
    const d = dom.window.document;
    d.getElementById("su-btn-install-db").click();
    await wait(200);
    d.getElementById("su-db-pass").value = "secret66";
    d.getElementById("su-db-pass2").value = "secret66";
    d.getElementById("su-db-basedir").value = "/opt/mysql";
    d.getElementById("su-db-start").click();
    await wait(80);           // 确认框出现
    d.getElementById("confirm-ok").click();
    await wait(2600);          // 首次轮询在 2s 后
    expect(posted).toBeTruthy();
    expect(posted.root_password).toBe("secret66");
    expect(posted.install_service).toBe(true);   // 默认勾选(需求确认项)
    // 完成后连接信息回填第 5 步
    expect(d.getElementById("su-cf-host").value).toBe("127.0.0.1");
    expect(d.getElementById("su-cf-port").value).toBe("3307");
    expect(d.getElementById("su-cf-pass").value).toBe("secret66");
    expect(d.getElementById("su-db-status").textContent).toContain("安装完成");
    expect(calls.some((c) => c.url.includes("/api/setup/install-mysql/status"))).toBe(true);
  }, 15000);

  it("轮询到 failed 时错误可见且不回填连接", async () => {
    const routes = dbRoutes([
      { url: "/api/setup/install-mysql", method: "POST", data: { ok: true, status: "running" } },
      { url: "/api/setup/install-mysql/status",
        data: { status: "failed", percent: 70, phase: "extract", error: "压缩包不完整" } },
    ]);
    const { dom } = createDom(routes);
    await wait(60);
    await dom.window.openSetup(true);
    await wait(150);
    const d = dom.window.document;
    d.getElementById("su-btn-install-db").click();
    await wait(200);
    d.getElementById("su-db-pass").value = "secret66";
    d.getElementById("su-db-pass2").value = "secret66";
    d.getElementById("su-db-basedir").value = "/opt/mysql";
    d.getElementById("su-db-start").click();
    await wait(80);
    d.getElementById("confirm-ok").click();
    await wait(2600);
    expect(d.getElementById("su-db-progress-text").textContent).toContain("压缩包不完整");
    expect(d.getElementById("su-db-status").className).toContain("err");
    expect(d.getElementById("su-db-start").disabled).toBe(false);   // 可重试
  }, 15000);
});
