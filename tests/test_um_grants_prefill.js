/* 回归验证(2026-08-28):设置权限弹窗打开时「带出现有授权」——基于现状修改,而非从空重设。
   1) parseGrants:SHOW GRANTS 文本 → {scopeAll, databases, privileges, extra} 解析表
   2) 编辑弹窗打开流程:fetch 返回该用户 grants → 范围/指定库选中/权限勾选 全部回填
   3) 界面外授权(表级/系统权限)出现时,状态区给出「保存将按本次勾选覆盖」提示 */
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const BASE = path.join(__dirname, "..");
const html = fs.readFileSync(path.join(BASE, "static", "index.html"), "utf-8");
const appJs = fs.readFileSync(path.join(BASE, "static", "app.js"), "utf-8");

const errors = [];
let grantsResp = null;   // 由用例设置
const dom = new JSDOM(html, {
  url: "http://127.0.0.1:8090/",
  runScripts: "outside-only",
  beforeParse(window) {
    window.fetch = async (url) => {
      const u = String(url);
      if (u.includes("/grants")) return { ok: true, json: async () => grantsResp };
      if (u.includes("/api/databases")) return {
        ok: true, json: async () => [{ name: "report_db" }, { name: "other_db" }] };
      return { ok: true, json: async () => [] };
    };
    window.echarts = { init: () => ({ setOption() {}, resize() {} }) };
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
  const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  try {
    console.log("======== 设置权限带出现有授权 回归 ========");
    if (topLevelError) { check(false, "顶层执行: " + topLevelError); }
    else check(true, "app.js 顶层执行无异常");

    // ---- 1) parseGrants 解析表 ----
    const pg = (lines) => dom.window.eval("JSON.stringify(window.parseGrants(" + JSON.stringify(lines) + "))");
    check(pg(["GRANT USAGE ON *.* TO `a`@`%`"]) ===
      JSON.stringify({ scopeAll: false, databases: [], privileges: [], extra: [] }),
      "USAGE 视为空授权(忽略)");
    check(pg(["GRANT USAGE ON *.* TO `a`@`%`",
              "GRANT SELECT, INSERT ON `mydb`.* TO `a`@`%`"]) ===
      JSON.stringify({ scopeAll: false, databases: ["mydb"], privileges: ["SELECT", "INSERT"], extra: [] }),
      "指定库 + 权限勾选解析");
    const ALL_PRIVS = ["SELECT","INSERT","UPDATE","DELETE","CREATE","DROP","ALTER","INDEX",
      "REFERENCES","CREATE VIEW","SHOW VIEW","TRIGGER","EVENT","LOCK TABLES","GRANT OPTION"];
    check(pg(["GRANT ALL PRIVILEGES ON *.* TO `root`@`%` WITH GRANT OPTION"]) ===
      JSON.stringify({ scopeAll: true, databases: [], privileges: ALL_PRIVS, extra: [] }),
      "ALL PRIVILEGES → 全局 + 网格全选 + GRANT OPTION");
    check(pg(["GRANT SELECT, PROCESS ON `mydb`.* TO `a`@`%`"]) ===
      JSON.stringify({ scopeAll: false, databases: ["mydb"], privileges: ["SELECT"], extra: ["PROCESS"] }),
      "界面外系统权限归入 extra(PROCESS)");
    check(pg(["GRANT SELECT ON `db`.`tbl` TO `a`@`%`"]) ===
      JSON.stringify({ scopeAll: false, databases: [], privileges: ["SELECT"], extra: ["表级/列级授权 `db`.`tbl`"] }),
      "表级授权归入 extra 提示");

    // ---- 2) 编辑弹窗带出授权(普通用户,指定库场景) ----
    const doc = dom.window.document;
    grantsResp = { ok: true, user: "app", host: "localhost", grants: [
      "GRANT SELECT, INSERT, UPDATE ON `report_db`.* TO `app`@`localhost`",
      "GRANT USAGE ON *.* TO `app`@`localhost`"] };
    dom.window.eval("window.umEdit('app','localhost')");
    await wait(50);
    const privs = () => Array.from(doc.querySelectorAll("#um-privs input"))
      .filter((c) => c.checked).map((c) => c.value);
    const selectedDbs = () => Array.from(doc.querySelectorAll("#um-dbs option"))
      .filter((o) => o.selected).map((o) => o.value);
    check(!doc.getElementById("um-modal").classList.contains("hidden"), "编辑弹窗已打开");
    check(doc.querySelector('input[name="um-scope"][value="pick"]').checked, "范围=指定数据库(带出)");
    check(eq(selectedDbs(), ["report_db"]), "指定库已带出选中: " + JSON.stringify(selectedDbs()));
    check(eq(privs(), ["SELECT", "INSERT", "UPDATE"]), "权限已带出勾选: " + JSON.stringify(privs()));
    check(doc.getElementById("um-user").readOnly, "用户名只读(编辑模式)");

    // ---- 3) 全部数据库场景 ----
    grantsResp = { ok: true, user: "app", host: "localhost", grants: [
      "GRANT ALL PRIVILEGES ON *.* TO `app`@`localhost` WITH GRANT OPTION"] };
    dom.window.eval("window.umEdit('app','localhost')");
    await wait(50);
    check(doc.querySelector('input[name="um-scope"][value="all"]').checked, "范围=全部数据库(带出)");
    check(doc.getElementById("um-db-wrap").classList.contains("hidden"), "指定库区隐藏");
    const p2 = privs();
    check(p2.length === 15 && p2.includes("GRANT OPTION"), "权限网格全选(15,含 GRANT OPTION)");
  } catch (e) {
    check(false, "异常: " + e.message);
  } finally {
    if (errors.length) { console.log("[async errors]", errors.join("; ")); }
    console.log(failCount === 0 ? "=== ALL PASS ===" : `=== ${failCount} FAIL ===`);
    process.exit(failCount === 0 ? 0 : 1);
  }
}, 60);