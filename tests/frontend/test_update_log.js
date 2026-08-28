/* 针对性验证:软件更新面板「最新版本更新日志」无条件展示(#up-latest-log)。
   走真实交互路径:点击「系统设置」导航 → switchPage("settings") → 真实 loadUpdatePanel()。
   通过 fetch 对 /api/update/badge 返回可控 JSON,分场景断言:
   1) 已是最新(无更新)→ 仍显示更新日志(需求核心)
   2) 有最新版本 → 显示更新日志
   3) 离线 → 离线占位
   并断言区域:位于最新版本下方、检查频率上方,始终可见。 */
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const BASE = path.join(__dirname, "..", "..");
const html = fs.readFileSync(path.join(BASE, "src", "static", "index.html"), "utf-8");
const appJs = fs.readFileSync(path.join(BASE, "src", "static", "app.js"), "utf-8");

let badgeResp = { current: "3.4.1", latest: "3.4.1", has_update: false, offline: false, body: "默认日志" };
let okCount = 0, failCount = 0;
function check(cond, msg) { (cond ? okCount++ : failCount++); console.log(`[${cond ? "OK" : "FAIL"}] ${msg}`); return cond; }

const dom = new JSDOM(html, { url: "http://127.0.0.1:8090/", runScripts: "outside-only",
  beforeParse(window) {
    window.fetch = async (url) => {
      const u = String(url);
      if (u.includes("/api/update/badge") || u.includes("/api/update/check")) return { ok: true, json: async () => badgeResp };
      if (u.includes("/api/settings")) return { ok: true, json: async () => ({ update_check_interval: "weekly" }) };
      return { ok: true, json: async () => [] };
    };
    window.echarts = { init: () => ({ setOption() {}, resize() {} }) };
    window.confirm = () => true; window.alert = () => {};
    window.addEventListener("error", (e) => { /* suppress */ });
  } });
let topErr = null;
try { dom.window.eval(appJs); } catch (e) { topErr = e.message; }

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
async function goSettings() {
  const nav = dom.window.document.querySelector('.nav-item[data-page="settings"]');
  if (nav && typeof nav.onclick === "function") nav.onclick();
  await wait(40);
}

setTimeout(async () => {
  if (topErr) { console.log("[FAIL] 顶层:", topErr); process.exit(1); }
  const doc = dom.window.document;
  console.log("======== 最新版本更新日志无条件展示验证 ========");
  const upLatest = doc.getElementById("up-latest");
  const logEl = doc.getElementById("up-latest-log");
  const upInterval = doc.getElementById("up-interval");
  check(!!logEl, "#up-latest-log 元素存在");
  // 2026-08-28:更新日志只在上方 #up-latest-log 展示一次,重复区 #up-changelog 已移除
  check(!doc.querySelector("#up-changelog"), "#up-changelog 重复日志区已移除");

  // 结构位置(仅当元素都在时)
  if (logEl && upLatest && upInterval) {
    const DPF = dom.window.Node.DOCUMENT_POSITION_FOLLOWING;
    const latestToLog = (upLatest.compareDocumentPosition(logEl) & DPF) !== 0;
    const logToInterval = (logEl.compareDocumentPosition(upInterval) & DPF) !== 0;
    check(latestToLog, "更新日志位于最新版本下方");
    check(logToInterval, "更新日志位于检查频率上方");
  }

  // 场景1: 已是最新(核心需求)
  badgeResp = { current: "3.4.1", latest: "3.4.1", has_update: false, offline: false, body: "v3.4.1 更新内容" };
  await goSettings();
  check(logEl.textContent.includes("v3.4.1 更新内容"), `已是最新仍显示日志: "${logEl.textContent.slice(0,50)}"`);

  // 场景2: 有更新
  badgeResp = { current: "3.3.0", latest: "3.4.1", has_update: true, offline: false, body: "发现新版本内容" };
  await goSettings();
  check(logEl.textContent.includes("发现新版本内容"), "有更新时显示更新日志");

  // 场景3: 离线
  badgeResp = { current: "3.4.1", latest: "", has_update: false, offline: true };
  await goSettings();
  check(logEl.textContent.includes("离线"), `离线占位: "${logEl.textContent}"`);

  // 始终可见
  check(!logEl.classList.contains("hidden"), "#up-latest-log 始终可见(不带 hidden)");

  console.log(failCount === 0 ? "=== ALL PASS ===" : `=== ${failCount} FAIL ===`);
  process.exit(failCount === 0 ? 0 : 1);
}, 100);