/* 回归验证(2026-08-28):root 用户「设置权限」前端拦截。
   需求:查看授权=只读列表(无论普通/root 都列出);设置权限=仅普通用户可改;
   root 点「设置权限」→ 提示不允许修改,编辑弹窗不打开(查看授权走 um-view-grants 只读展示)。
   后端 PUT /api/users/<root@host> 编辑授权分支另有 403 双端保护(见 server.py)。 */
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const BASE = path.join(__dirname, "..", "..");
const html = fs.readFileSync(path.join(BASE, "src", "static", "index.html"), "utf-8");
const appJs = fs.readFileSync(path.join(BASE, "src", "static", "app.js"), "utf-8");

const errors = [];
const dom = new JSDOM(html, {
  url: "http://127.0.0.1:8090/",
  runScripts: "outside-only",
  beforeParse(window) {
    window.fetch = async () => ({ ok: true, json: async () => [] });
    window.echarts = { init: () => ({ setOption() {}, resize() {}, getDataURL: () => "" }), getInstanceByDom: () => null };
    window.confirm = () => true;
    window.alert = () => {};
    window.addEventListener("error", (e) => errors.push(e.message));
  },
});
let topLevelError = null;
try { dom.window.eval(appJs); } catch (e) { topLevelError = e.message; }

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

setTimeout(async () => {
  let okCount = 0, failCount = 0;
  function check(cond, msg) { (cond ? okCount++ : failCount++); console.log(`[${cond ? "OK" : "FAIL"}] ${msg}`); }
  try {
    console.log("======== root 授权保护 回归 ========");
    if (topLevelError) { check(false, "顶层执行: " + topLevelError); }
    else check(true, "app.js 顶层执行无异常");
    const doc = dom.window.document;
    const um = doc.getElementById("um-modal");
    const cfm = doc.getElementById("confirm-modal");

    // 场景1:root 点「设置权限」→ 编辑弹窗不打开,提示弹窗打开
    dom.window.eval("window.umEdit('root','localhost')");
    await wait(30);
    check(cfm && !cfm.classList.contains("hidden"), "root 设置权限:提示弹窗已打开");
    check(um && um.classList.contains("hidden"), "root 设置权限:编辑弹窗未打开");
    check(doc.getElementById("confirm-title").textContent.includes("不允许修改"),
      "提示标题含「不允许修改 root 授权」");
    // 关闭提示弹窗(点确认)
    if (cfm) { doc.getElementById("confirm-ok").click(); }
    await wait(10);
    check(cfm && cfm.classList.contains("hidden"), "提示弹窗可正常关闭");

    // 场景2:普通用户点「设置权限」→ 编辑弹窗正常打开
    dom.window.eval("window.umEdit('app_user','localhost')");
    await wait(30);
    check(um && !um.classList.contains("hidden"), "普通用户设置权限:编辑弹窗已打开");
    check(doc.getElementById("um-user").readOnly === true, "编辑弹窗用户名只读");
  } catch (e) {
    check(false, "异常: " + e.message);
  } finally {
    if (errors.length) { console.log("[async errors]", errors.join("; ")); }
    console.log(failCount === 0 ? "=== ALL PASS ===" : `=== ${failCount} FAIL ===`);
    process.exit(failCount === 0 ? 0 : 1);
  }
}, 60);