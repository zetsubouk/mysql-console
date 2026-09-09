/* 前端运行时验证:在 jsdom 中执行 app.js,检查顶层绑定与导航点击是否生效 */
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const BASE = path.join(__dirname, "..", "..");  // 仓库根(脚本位于 tests/frontend/, 资源在 src/static/)
const html = fs.readFileSync(path.join(BASE, "src", "static", "index.html"), "utf-8");
const appJs = fs.readFileSync(path.join(BASE, "src", "static", "app.js"), "utf-8");

const errors = [];
const dom = new JSDOM(html, {
  url: "http://127.0.0.1:8090/",
  runScripts: "outside-only",
  beforeParse(window) {
    window.fetch = async () => ({ ok: true, json: async () => [] });
    window.echarts = {
      init: () => ({ setOption() {}, resize() {}, getDataURL: () => "" }),
      getInstanceByDom: () => null,   // 图表导出按钮遍历时无实例则跳过
    };
    window.confirm = () => true;
    window.alert = () => {};
    window.addEventListener("error", (e) => errors.push(e.message));
  },
});

let topLevelError = null;
try {
  dom.window.eval(appJs);
} catch (e) {
  topLevelError = e.message;
}

setTimeout(() => {
  const doc = dom.window.document;
  // 断言统一走 check():失败累计 failCount 并决定退出码。
  // (历史上本文件只 console.log 不计失败,退出码恒 0——测试假绿,2026-09-10 修复)
  let failCount = 0;
  function check(cond, msg) {
    console.log(`[${cond ? "OK" : "FAIL"}] ${msg}`);
    if (!cond) failCount++;
  }
  console.log("======== 验证结果 ========");
  if (topLevelError) {
    console.log("[FAIL] 顶层执行错误:", topLevelError);
    process.exit(1);
  }
  console.log("[OK] app.js 顶层执行无异常");

  const navItems = doc.querySelectorAll(".nav-item");
  let bound = 0;
  navItems.forEach((n) => { if (typeof n.onclick === "function") bound++; });
  check(bound === navItems.length, `导航绑定: ${bound}/${navItems.length}`);

  // 模拟点击「连接管理」
  const connNav = doc.querySelector('.nav-item[data-page="connections"]');
  const page = doc.getElementById("page-connections");
  connNav.onclick();
  const switched = !page.classList.contains("hidden");
  check(switched, `点击「连接管理」页面切换${switched ? "成功" : "失败"}`);
  if (!switched) { console.log("当前 page-connections class:", page.className); }

  // 模拟点击「备份与还原」
  const bkNav = doc.querySelector('.nav-item[data-page="backup"]');
  const bkPage = doc.getElementById("page-backup");
  bkNav.onclick();
  const bkSwitched = !bkPage.classList.contains("hidden");
  check(bkSwitched, `点击「备份与还原」页面切换${bkSwitched ? "成功" : "失败"}`);

  // 连接管理按钮绑定检查
  const newBtn = doc.getElementById("btn-new-conn");
  const testBtn = doc.getElementById("btn-test-conn");
  const saveBtn = doc.getElementById("btn-save-conn");
  check(typeof newBtn.onclick === "function", "新建连接按钮绑定");
  check(typeof testBtn.onclick === "function", "测试连接按钮绑定");
  check(typeof saveBtn.onclick === "function", "保存连接按钮绑定");

  // —— 远程服务器类型(2026-08-31 新增)——
  const remoteOsSel = doc.getElementById("cf-remote-os");
  const remoteGuide = doc.getElementById("cf-remote-guide");
  const remoteCheckBtn = doc.getElementById("cf-btn-remote-check");
  check(!!remoteOsSel, "#cf-remote-os 服务器类型下拉存在");
  check(!!remoteGuide, "#cf-remote-guide 指引面板存在");
  check(typeof remoteCheckBtn.onclick === "function", "测试远程环境按钮绑定");
  // connFormBody 携带 remote_os(window.* 桥)
  const body = dom.window.connFormBody();
  check("remote_os" in body, "connFormBody 携带 remote_os 字段");
  // —— 数据库版本选择(2026-09-01 方案A 新增)——
  const dvSel = doc.getElementById("cf-db-version");
  const dvOpts = dvSel ? [...dvSel.options].map((o) => o.value) : [];
  check(!!dvSel, "#cf-db-version 数据库版本下拉存在");
  check(["", "5.7", "8.x"].every((v) => dvOpts.includes(v)), "db_version 选项含自动/5.7/8.x");
  check("db_version" in body, "connFormBody 携带 db_version 字段");
  // updateRemoteGuide:windows 显示指引 / 空隐藏(change 事件驱动)
  remoteOsSel.value = "windows";
  remoteOsSel.dispatchEvent(new dom.window.Event("change"));
  check(!remoteGuide.classList.contains("hidden") && remoteGuide.innerHTML.indexOf("Git Bash") >= 0,
        "Windows 指引含 Git Bash 配置");
  remoteOsSel.value = "";
  remoteOsSel.dispatchEvent(new dom.window.Event("change"));
  check(remoteGuide.classList.contains("hidden"), "空类型隐藏指引");
  // updateBackupPathFields:远程主机显示远程备份区 / 本地主机显示本地备份区(input 事件驱动)
  const hostInput = doc.getElementById("cf-host");
  hostInput.value = "db.example.com";
  hostInput.dispatchEvent(new dom.window.Event("input"));
  const remoteBox = doc.getElementById("cf-backup-remote");
  const localBox = doc.getElementById("cf-backup-local");
  check(!remoteBox.classList.contains("hidden"), "远程主机显示远程备份目录区");
  check(localBox.classList.contains("hidden"), "远程主机隐藏本地备份目录区");
  hostInput.value = "127.0.0.1";
  hostInput.dispatchEvent(new dom.window.Event("input"));
  check(!localBox.classList.contains("hidden"), "本地主机显示本地备份目录区");

  // 备份还原按钮绑定
  const backupBtn = doc.getElementById("btn-backup");
  const restoreBtn = doc.getElementById("btn-restore");
  check(typeof backupBtn.onclick === "function", "执行备份按钮绑定");
  check(typeof restoreBtn.onclick === "function", "执行还原按钮绑定");

  // —— 远程还原文件区(2026-08-31 新增)——
  const rsLocalBox = doc.getElementById("rs-local-file-box");
  const rsRemoteBox = doc.getElementById("rs-remote-file-box");
  check(!!rsLocalBox, "#rs-local-file-box 存在");
  check(!!rsRemoteBox, "#rs-remote-file-box 存在");
  check(rsLocalBox && !rsLocalBox.classList.contains("hidden"), "默认(无连接/本地)本地文件区显示");
  check(rsRemoteBox && rsRemoteBox.classList.contains("hidden"), "默认远程文件区隐藏");
  check(typeof doc.getElementById("btn-rs-remote-refresh").onclick === "function", "远程文件刷新按钮绑定");

  // 告警阈值:保存按钮绑定 + 输入框存在(fetch stub 返回 [],应安全容错)
  const saveAlertBtn = doc.getElementById("btn-save-alert-settings");
  check(typeof saveAlertBtn.onclick === "function", "告警阈值保存按钮绑定");
  ["alert-max-conn", "alert-max-slow", "alert-max-running"].forEach((id) => {
    check(!!doc.getElementById(id), `阈值输入框 #${id} 存在`);
  });

  // —— 用户管理 / 数据库重启(2026-08-27 新增)——
  // 用户与连接页:用户管理面板按钮与三个弹窗
  const newUserBtn = doc.getElementById("btn-new-user");
  check(typeof newUserBtn.onclick === "function", "新增用户按钮绑定");
  ["um-modal", "um-pwd-modal", "um-grants-modal"].forEach((id) => {
    check(!!doc.getElementById(id), `弹窗 #${id} 存在`);
  });
  ["um-user", "um-host", "um-pass", "um-dbs", "um-privs"].forEach((id) => {
    check(!!doc.getElementById(id), `用户表单 #${id} 存在`);
  });
  // 点击「用户与连接」页:触发 loadUserMgmt/loadUsers/loadProcesslist(fetch 返回 [], 应容错)
  const usersNav = doc.querySelector('.nav-item[data-page="users"]');
  usersNav.onclick();
  const usersSwitched = !doc.getElementById("page-users").classList.contains("hidden");
  check(usersSwitched, `点击「用户与连接」页面切换${usersSwitched ? "成功" : "失败"}`);
  // 数据库页:重启按钮 + 状态徽标
  const restartBtn = doc.getElementById("btn-restart-db");
  check(typeof restartBtn.onclick === "function", "重启数据库按钮绑定");
  check(!!doc.getElementById("db-svc-status"), "#db-svc-status 状态徽标存在");
  // 点击「数据库」页:触发 loadDatabases + loadDbServiceStatus(fetch 返回 [], 应安全容错)
  const dbNav = doc.querySelector('.nav-item[data-page="databases"]');
  dbNav.onclick();
  const dbSwitched = !doc.getElementById("page-databases").classList.contains("hidden");
  check(dbSwitched, `点击「数据库」页面切换${dbSwitched ? "成功" : "失败"}`);

  if (errors.length) {
    console.log("[WARN] 捕获到异步错误:", errors.join("; "));
  } else {
    console.log("[OK] 无异步运行时错误");
  }
  if (failCount > 0) {
    console.log(`======== 结果: ${failCount} 项失败 ========`);
    process.exit(1);
  }
  console.log("======== 结果: 全部通过 ========");
  process.exit(0);
}, 400);
