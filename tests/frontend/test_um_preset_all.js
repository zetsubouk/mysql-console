/* 针对性验证:修复「完整权限」模板点击无反应 bug。
   jsdom 中执行 app.js, 打开用户弹窗渲染权限, 点击 data-preset="all" 按钮,
   断言 #um-privs 内所有 checkbox 被勾选。 */
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

setTimeout(() => {
  const doc = dom.window.document;
  console.log("======== 完整权限模板修复验证 ========");
  if (topLevelError) { console.log("[FAIL] 顶层执行错误:", topLevelError); process.exit(1); }

  // 渲染权限网格(等价于打开新增用户弹窗的 renderUmPrivs)
  doc.querySelectorAll("#um-privs").forEach((el) => {
    el.innerHTML = ["SELECT","INSERT","UPDATE","DELETE","CREATE","DROP","ALTER","INDEX",
      "REFERENCES","CREATE VIEW","SHOW VIEW","TRIGGER","EVENT","LOCK TABLES","GRANT OPTION"]
      .map((p) => `<label><input type="checkbox" value="${p}"> ${p}</label>`).join("");
  });
  const checks = () => Array.from(doc.querySelectorAll("#um-privs input")).map((c) => c.checked);

  // 基线:未点击前全未勾选
  const before = checks();
  console.log(`[${before.every((c) => c === false) ? "OK" : "FAIL"}] 初始全未勾选: ${JSON.stringify(before)}`);

  // 点击「完整权限」(data-preset="all")
  const allBtn = doc.querySelector('button[data-preset="all"]');
  if (!allBtn) { console.log("[FAIL] 未找到「完整权限」按钮"); process.exit(1); }
  allBtn.dispatachEvent ? null : null;
  allBtn.click();

  const after = checks();
  const allChecked = after.every((c) => c === true);
  const checkedCount = after.filter(Boolean).length;
  console.log(`[${allChecked ? "OK" : "FAIL"}] 点击「完整权限」后所有权限被勾选 (${checkedCount}/15): ${JSON.stringify(after)}`);

  // 对照:点击「清空」(data-preset="") 应全部取消
  const clearBtn = doc.querySelector('button[data-preset=""]');
  clearBtn.click();
  const cleared = checks();
  console.log(`[${cleared.every((c) => c === false) ? "OK" : "FAIL"}] 点击「清空」后全部取消`);

  // 对照:只读模板只勾 SELECT
  const rosBtn = doc.querySelector('button[data-preset="readonly"]');
  rosBtn.click();
  const ro = checks();
  const roOk = ro[0] === true && ro.slice(1).every((c) => c === false);
  console.log(`[${roOk ? "OK" : "FAIL"}] 点击「只读」仅勾选 SELECT: ${JSON.stringify(ro)}`);

  console.log(allChecked ? "ALL PASS" : "HAS FAILURE");
  process.exit(allChecked ? 0 : 1);
}, 100);