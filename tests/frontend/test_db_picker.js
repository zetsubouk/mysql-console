/* 验证目标数据库选择弹窗:点按钮→弹窗打开→列出库→点选回填 */
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
    window.fetch = async (url) => ({
      ok: true,
      json: async () => {
        if (String(url).includes("/api/databases")) return [{ name: "oa", table_count: 4859, data_size: 1, index_size: 1, total_size: 2, charset: "utf8mb4" }];
        return [];
      },
    });
    window.echarts = { init: () => ({ setOption() {}, resize() {} }) };
    window.confirm = () => true;
    window.alert = () => {};
    window.addEventListener("error", (e) => errors.push(e.message));
  },
});
dom.window.eval(appJs);
const doc = dom.window.document;
function assert(c, m) { if (!c) { console.log("[FAIL]", m); process.exitCode = 1; } else console.log("[OK]", m); }

setTimeout(async () => {
  try {
    await (async function main() {
      const btn = doc.querySelector("#btn-pick-db");
      assert(btn && btn.onclick, "选择数据库按钮已绑定 onclick");
      assert(!doc.querySelector("#rs-db-list") && !doc.querySelector("datalist"), "datalist 已移除");
      await btn.onclick();
      assert(!doc.querySelector("#db-picker-modal").classList.contains("hidden"), "弹窗已打开");
      await new Promise((r) => setTimeout(r, 30));
      const rows = doc.querySelectorAll("#db-picker-list .db-picker-row");
      assert(rows.length === 1, "数据库列表渲染 " + rows.length + " 行");
      assert(rows[0].textContent.includes("oa"), "列表含库名 oa");
      assert(rows[0].textContent.includes("4859"), "列表含表数量");
      rows[0].onclick();
      assert(doc.querySelector("#rs-target-db").value === "oa", "点选后回填 rs-target-db=" + doc.querySelector("#rs-target-db").value);
      assert(doc.querySelector("#db-picker-modal").classList.contains("hidden"), "点选后弹窗关闭");
      await btn.onclick();
      await new Promise((r) => setTimeout(r, 30));
      const flt = doc.querySelector("#db-picker-filter");
      flt.value = "zzz";
      flt.oninput();
      assert(doc.querySelectorAll("#db-picker-list .db-picker-row").length === 0, "搜索无匹配时列表为空");
      doc.querySelector("#db-picker-new-name").value = "newdb";
      doc.querySelector("#db-picker-apply-new").onclick();
      assert(doc.querySelector("#rs-target-db").value === "newdb", "应用新库名回填 " + doc.querySelector("#rs-target-db").value);
      await btn.onclick(); doc.querySelector("#db-picker-clear").onclick();
      assert(doc.querySelector("#rs-target-db").value === "", "留空清空目标库");
      if (errors.length) { console.log("[async errors]", errors); process.exitCode = 1; }
    })();
  } catch (e) { console.log("[FATAL]", e.message); process.exitCode = 1; }
  finally { setTimeout(() => process.exit(process.exitCode || 0), 10); }
}, 60);