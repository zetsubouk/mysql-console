/* MySQL Console 前端逻辑 */
"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const PAGES = {
  overview: { title: "概览监控" }, dashboard: { title: "数据看板" },
  variables: { title: "服务器变量" }, databases: { title: "数据库" },
  users: { title: "用户与连接" }, backup: { title: "备份与还原" },
  schedule: { title: "定时备份" },
  settings: { title: "系统设置" },
  alerts: { title: "告警中心" },
  connections: { title: "连接管理" }, logs: { title: "操作日志" },
};

/* ---------- API ---------- */
async function api(method, path, body) {
  const opt = { method, headers: {} };
  if (body !== undefined) {
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  // 携带认证 token
  const token = localStorage.getItem("mc_token");
  if (token) opt.headers["Authorization"] = "Bearer " + token;
  const res = await fetch(path, opt);
  let data = null;
  try { data = await res.json(); } catch (e) {}
  if (res.status === 401) {
    localStorage.removeItem("mc_token");
    if (location.pathname !== "/login.html") location.href = "/login.html";
  }
  if (!res.ok) throw new Error(data && data.error ? data.error : `HTTP ${res.status}`);
  return data;
}
const get = (p) => api("GET", p);
const post = (p, b) => api("POST", p, b || {});
const put = (p, b) => api("PUT", p, b || {});
const del = (p) => api("DELETE", p);

/* ---------- 工具 ---------- */
function fmtSize(bytes) {
  if (bytes === null || bytes === undefined) return "-";
  const n = Number(bytes);
  if (n < 1024) return n + " B";
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return v.toFixed(v >= 100 ? 0 : 1) + " " + units[i];
}
function fmtTime(ts) {
  if (!ts) return "-";
  const d = new Date(ts * 1000);
  const p = (x) => String(x).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
function setStatus(el, text, cls) {
  el.textContent = text || "";
  el.className = "inline-status" + (cls ? " " + cls : "");
}
function confirmDialog(title, body) {
  return new Promise((resolve) => {
    const m = $("#confirm-modal");
    $("#confirm-title").textContent = title;
    $("#confirm-body").innerHTML = body;
    m.classList.remove("hidden");
    const done = (v) => { m.classList.add("hidden"); cleanup(); resolve(v); };
    const cleanup = () => {
      $("#confirm-ok").onclick = null; $("#confirm-cancel").onclick = null;
    };
    $("#confirm-ok").onclick = () => done(true);
    $("#confirm-cancel").onclick = () => done(false);
  });
}
function toast(text, ok = true) {
  const el = document.createElement("div");
  el.style.cssText = `position:fixed;top:70px;right:24px;z-index:200;padding:10px 18px;border-radius:8px;font-size:13px;color:#fff;background:${ok ? "#3b6d11" : "#a32d2d"};box-shadow:0 4px 14px rgba(0,0,0,.15)`;
  el.textContent = text;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3200);
}

/* ---------- 页面切换 ---------- */
function switchPage(name) {
  $$(".nav-item").forEach((el) => el.classList.toggle("active", el.dataset.page === name));
  $$(".page").forEach((el) => el.classList.toggle("hidden", el.id !== "page-" + name));
  $("#page-title").textContent = PAGES[name].title;
  if (name === "overview") loadOverview();
  if (name === "databases") { loadDatabases(); loadDbServiceStatus(); }
  if (name === "users") { loadUserMgmt(); loadUsers(); loadProcesslist(); }
  if (name === "backup") { loadBackupPage(); }
  if (name === "schedule") loadSchedule();
  if (name === "connections") loadConnections();
  if (name === "settings") loadUpdatePanel();
  if (name === "logs") loadLogs();
  if (name === "settings") loadSettingsPage();
  if (name === "dashboard") loadDashboardPage();
  if (name === "alerts") loadAlertsPage();
  if (name === "variables") loadVariablesPage();
}

/* ---------- 连接管理 ---------- */
let connList = [];
let editingConnId = null;

async function loadConnections() {
  connList = await get("/api/connections") || [];
  renderConnSelect();
  renderConnTable();
}

function renderConnSelect() {
  const sel = $("#conn-select");
  const cur = sel.value;
  sel.innerHTML = '<option value="">未选择连接</option>' +
    connList.map((c) => `<option value="${c.id}">${c.active ? "● " : ""}${esc(c.name)} (${esc(c.host)}:${c.port})</option>`).join("");
  if (cur && connList.some((c) => c.id === cur)) sel.value = cur;
}

function renderConnTable() {
  const tb = $("#conn-table tbody");
  if (!connList.length) {
    tb.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--text-3);padding:24px">暂无连接配置,点击右上角「新建连接」添加,本机或远程 MySQL 均可</td></tr>';
    return;
  }
  const scopeBadge = (c) => {
    const h = String(c.host || "").toLowerCase();
    const local = h === "127.0.0.1" || h === "localhost" || h === "::1";
    return local
      ? '<span class="badge success">本机</span>'
      : '<span class="badge backup">远程</span>';
  };
  tb.innerHTML = connList.map((c) => `
    <tr>
      <td class="mono">${esc(c.name)}</td>
      <td>${scopeBadge(c)} ${esc(c.host)}</td>
      <td class="mono">${c.port}</td>
      <td class="mono">${esc(c.user)}</td>
      <td>${c.has_password ? "已设置" : '<span style="color:var(--text-3)">未设置</span>'}</td>
      <td class="ellipsis">${esc(c.note || "")}</td>
      <td>${c.active ? '<span class="badge success">已激活</span>' : '<span class="badge off">未激活</span>'}</td>
      <td>
        ${c.active
          ? '<button class="btn btn-sm" disabled>使用中</button>'
          : `<button class="btn btn-sm op-btn" onclick="activateConn('${c.id}')">激活</button>`}
        <button class="btn btn-sm op-btn" onclick="editConn('${c.id}')">编辑</button>
        <button class="btn btn-sm btn-danger op-btn" onclick="removeConn('${c.id}')">删除</button>
      </td>
    </tr>`).join("");
}

async function activateConn(id) {
  try {
    await post("/api/connect", { id });
    toast("连接激活成功");
    updateConnStatus(true);
    await loadConnections();
    loadOverview();
  } catch (e) { toast(e.message, false); }
}

async function removeConn(id) {
  if (!(await confirmDialog("删除连接", "确定删除该连接配置吗?"))) return;
  await del("/api/connections/" + id);
  toast("已删除");
  loadConnections();
}

function editConn(id) {
  editingConnId = id;
  const c = connList.find((x) => x.id === id);
  $("#conn-form-title").textContent = "编辑连接";
  $("#cf-name").value = c.name; $("#cf-host").value = c.host;
  $("#cf-port").value = c.port; $("#cf-user").value = c.user;
  $("#cf-pass").value = ""; $("#cf-note").value = c.note || "";
  $("#conn-form-panel").classList.remove("hidden");
  setStatus($("#cf-status"), "");
}

$("#btn-new-conn").onclick = () => {
  editingConnId = null;
  $("#conn-form-title").textContent = "新建连接";
  ["cf-name", "cf-host", "cf-port", "cf-user", "cf-pass", "cf-note"].forEach((id) => {
    if (id === "cf-host") $("#" + id).value = "127.0.0.1";
    else if (id === "cf-port") $("#" + id).value = 3306;
    else if (id === "cf-user") $("#" + id).value = "root";
    else $("#" + id).value = "";
  });
  $("#conn-form-panel").classList.remove("hidden");
  setStatus($("#cf-status"), "");
};
$("#btn-conn-cancel").onclick = () => $("#conn-form-panel").classList.add("hidden");

$("#btn-test-conn").onclick = async () => {
  const body = connFormBody();
  const st = $("#cf-status");
  setStatus(st, "测试中...");
  try {
    const r = await post("/api/connections/test", body);
    setStatus(st, r.ok ? `连接成功: ${r.version}` : `失败: ${r.error}`, r.ok ? "ok" : "err");
  } catch (e) { setStatus(st, "测试异常: " + e.message, "err"); }
};

function connFormBody() {
  return {
    name: $("#cf-name").value || "未命名",
    host: $("#cf-host").value || "127.0.0.1",
    port: parseInt($("#cf-port").value || 3306),
    user: $("#cf-user").value || "root",
    password: $("#cf-pass").value,
    note: $("#cf-note").value,
  };
}

$("#btn-save-conn").onclick = async () => {
  try {
    if (editingConnId) await put("/api/connections/" + editingConnId, connFormBody());
    else await post("/api/connections", connFormBody());
    toast("连接已保存");
    $("#conn-form-panel").classList.add("hidden");
    loadConnections();
  } catch (e) { toast(e.message, false); }
};

/* ---------- 连接状态 ---------- */
let connActive = false;
function updateConnStatus(ok) {
  connActive = ok;
  const el = $("#conn-status");
  el.textContent = ok ? "已连接" : "未连接";
  el.className = "status-dot" + (ok ? " ok" : "");
}

$("#conn-select").onchange = async (e) => {
  const id = e.target.value;
  if (!id) { updateConnStatus(false); return; }
  try { await post("/api/connect", { id }); updateConnStatus(true); toast("已切换到 " + id); }
  catch (err) { updateConnStatus(false); toast("激活失败: " + err.message, false); }
};

/* ---------- 概览监控 ---------- */
let connChart, qpsChart;
const connSeries = [], qpsSeries = [];

function initCharts() {
  const base = { grid: { left: 46, right: 16, top: 34, bottom: 26 }, tooltip: { trigger: "axis" } };
  connChart = echarts.init($("#chart-conn"));
  connChart.setOption({
    ...base,
    title: { text: "连接数", textStyle: { fontSize: 13, fontWeight: 500 } },
    xAxis: { type: "category", data: [] },
    yAxis: { type: "value", minInterval: 1 },
    series: [{ type: "line", smooth: true, showSymbol: false, data: connSeries, lineStyle: { width: 2, color: "#185fa5" }, itemStyle: { color: "#185fa5" }, areaStyle: { opacity: 0.08, color: "#185fa5" } }],
  });
  qpsChart = echarts.init($("#chart-qps"));
  qpsChart.setOption({
    ...base,
    title: { text: "每秒查询数 QPS", textStyle: { fontSize: 13, fontWeight: 500 } },
    xAxis: { type: "category", data: [] },
    yAxis: { type: "value", minInterval: 1 },
    series: [{ type: "line", smooth: true, showSymbol: false, data: qpsSeries, lineStyle: { width: 2, color: "#1d9e75" }, itemStyle: { color: "#1d9e75" }, areaStyle: { opacity: 0.08, color: "#1d9e75" } }],
  });
}

async function loadOverview() {
  try {
    const ov = await get("/api/overview");
    renderOverviewCards(ov);
    updateConnStatus(true);
  } catch (e) {
    updateConnStatus(false);
    $("#overview-cards").innerHTML = `<div class="panel" style="color:var(--text-2)">${esc(e.message)}</div>`;
  }
  try {
    const dbs = await get("/api/databases");
    renderDbSummary(dbs);
  } catch (e) {
    $("#db-summary-table tbody").innerHTML = `<tr><td colspan="5" style="text-align:center;color:var(--text-3);padding:16px">加载失败: ${esc(e.message)}</td></tr>`;
  }
}

function renderOverviewCards(ov) {
  const hitRate = ov.innodb_buffer_hits
    ? ((ov.innodb_buffer_hits / (ov.innodb_buffer_hits + ov.innodb_buffer_reads)) * 100).toFixed(1)
    : "-";
  const cards = [
    { label: "MySQL 版本", value: ov.version, sub: `主机 ${ov.hostname} · 端口 ${ov.port}` },
    { label: "运行时长", value: ov.uptime_text, sub: `数据目录 ${ov.datadir}` },
    { label: "当前连接", value: ov.current_conn, sub: `上限 ${ov.max_conn}`, cls: ov.current_conn > ov.max_conn * 0.8 ? "warn" : "" },
    { label: "运行线程", value: ov.threads_running, sub: `已创建 ${ov.threads_created}` },
    { label: "缓存命中率", value: hitRate + "%", sub: `慢查询 ${ov.slow_queries} 次`, cls: hitRate !== "-" && parseFloat(hitRate) < 95 ? "warn" : "success" },
    { label: "打开的表", value: ov.open_tables, sub: `累计打开 ${ov.opened_tables}` },
  ];
  $("#overview-cards").innerHTML = cards.map((c) => `
    <div class="card">
      <div class="c-label">${c.label}</div>
      <div class="c-value ${c.cls || ""}">${esc(c.value)}</div>
      <div class="c-sub">${esc(c.sub || "")}</div>
    </div>`).join("");
}

function renderDbSummary(dbs) {
  const tb = $("#db-summary-table tbody");
  tb.innerHTML = dbs.slice(0, 8).map((d) => `
    <tr><td class="mono">${esc(d.name)}</td><td class="mono">${d.table_count}</td>
    <td class="mono">${fmtSize(d.data_size)}</td><td class="mono">${fmtSize(d.index_size)}</td>
    <td class="mono">${fmtSize(d.total_size)}</td></tr>`).join("");
}

async function monitorLoop() {
  if (!connActive) return;
  try {
    const m = await get("/api/monitor");
    const t = fmtTime(m.ts);
    connSeries.push(m.connections); qpsSeries.push(m.qps);
    if (connSeries.length > 60) { connSeries.shift(); qpsSeries.shift(); }
    connChart.setOption({ xAxis: { data: connSeries.map((_, i) => t) }, series: [{ data: connSeries }] });
    qpsChart.setOption({ xAxis: { data: qpsSeries.map(() => t) }, series: [{ data: qpsSeries }] });
  } catch (e) {}
}
setInterval(monitorLoop, 5000);

/* ---------- 数据库 ---------- */
async function loadDatabases() {
  try {
    const dbs = await get("/api/databases");
    $("#db-table tbody").innerHTML = dbs.map((d) => `
      <tr>
        <td class="mono">${esc(d.name)}</td><td class="mono">${d.table_count}</td>
        <td class="mono">${fmtSize(d.data_size)}</td><td class="mono">${fmtSize(d.index_size)}</td>
        <td class="mono">${fmtSize(d.total_size)}</td><td>${esc(d.charset)}</td>
        <td><button class="btn btn-sm op-btn" data-db="${esc(d.name)}" onclick="window.showDb(this)">查看表</button></td>
      </tr>`).join("") || '<tr><td colspan="7" style="text-align:center;color:var(--text-3);padding:20px">暂无业务数据库</td></tr>';
  } catch (e) {
    $("#db-table tbody").innerHTML = `<tr><td colspan="7" style="text-align:center;color:var(--text-3);padding:20px">加载失败: ${esc(e.message)}</td></tr>`;
    toast(e.message, false);
  }
}

async function showDbDetail(name) {
  try {
    const tables = await get("/api/databases/" + encodeURIComponent(name));
    $("#db-detail-title").textContent = `表结构 - ${name}`;
    $("#db-detail-table tbody").innerHTML = tables.map((t) => `
      <tr><td class="mono">${esc(t.name)}</td><td>${esc(t.engine)}</td>
      <td class="mono">${t.rows}</td><td class="mono">${fmtSize(t.data_size)}</td>
      <td class="mono">${fmtSize(t.index_size)}</td><td>${esc(t.create_time)}</td>
      <td class="ellipsis">${esc(t.comment)}</td></tr>`).join("") || '<tr><td colspan="7" style="text-align:center;color:var(--text-3);padding:16px">空库,无表</td></tr>';
    $("#db-detail-panel").classList.remove("hidden");
  } catch (e) { toast(e.message, false); }
}
window.showDb = function (el) { showDbDetail(el.dataset.db); };
$("#btn-back-db").onclick = () => $("#db-detail-panel").classList.add("hidden");
$("#btn-refresh-db").onclick = loadDatabases;

/* ---------- 用户与连接 ---------- */
async function loadUsers() {
  try {
    const users = await get("/api/users");
    $("#user-table tbody").innerHTML = users.map((u) => `
      <tr><td class="mono">${esc(u.user)}</td><td class="mono">${esc(u.host)}</td>
      <td>${u.has_password === "YES" ? "已设置" : '<span style="color:var(--text-3)">未设置</span>'}</td>
      <td class="mono">${u.privileges}</td><td class="mono">${esc(u.plugin)}</td>
      <td>${u.locked === "Y" ? '<span class="badge failed">锁定</span>' : '<span class="badge success">正常</span>'}</td></tr>`).join("");
  } catch (e) { toast(e.message, false); }
}

async function loadProcesslist() {
  try {
    const list = await get("/api/processlist");
    $("#proc-table tbody").innerHTML = list.map((p) => `
      <tr>
        <td class="mono">${p.id}</td><td class="mono">${esc(p.user)}</td>
        <td class="mono">${esc(p.host)}</td><td>${esc(p.db || "-")}</td>
        <td>${esc(p.command)}</td><td class="mono">${p.time}</td>
        <td class="ellipsis">${esc(p.state || "")}</td>
        <td class="ellipsis mono" title="${esc(p.info)}">${esc(p.info || "")}</td>
        <td>${p.command === "Sleep" ? "" : `<button class="btn btn-sm btn-danger" onclick="killProc(${p.id})">Kill</button>`}</td>
      </tr>`).join("") || '<tr><td colspan="9" style="text-align:center;color:var(--text-3);padding:20px">无活动连接</td></tr>';
  } catch (e) { toast(e.message, false); }
}
async function killProc(pid) {
  if (!(await confirmDialog("终止连接", `确定终止连接 ID ${pid} 吗?正在执行的事务可能回滚。`))) return;
  try { await post("/api/kill", { pid }); toast("已终止连接 " + pid); loadProcesslist(); }
  catch (e) { toast(e.message, false); }
}
$("#btn-refresh-proc").onclick = () => { loadUsers(); loadProcesslist(); };

/* ---------- 数据库服务状态 / 重启 ---------- */
async function loadDbServiceStatus() {
  const el = $("#db-svc-status");
  if (!el) return null;
  try {
    const st = await get("/api/service/status");
    const os = st.os_status;
    let txt = "状态未知", cls = "badge off";
    if (os === "running") {
      txt = (st.has_active_conn && st.db_reachable === false) ? "运行中·连不上" : "数据库运行中";
      cls = "badge running";
    } else if (os === "stopped") { txt = "数据库已停止"; cls = "badge failed"; }
    else if (os === "missing") { txt = "未检测到服务"; cls = "badge off"; }
    el.textContent = txt;
    el.className = cls;
    return st;
  } catch (e) { el.textContent = "状态未知"; el.className = "badge off"; return null; }
}
async function restartDb() {
  if (!(await confirmDialog("重启数据库",
      "确定重启本机 MySQL 服务吗？将短暂中断所有数据库连接；全量模式下系统库将暂不可用，重启后自动恢复。"))) return;
  const btn = $("#btn-restart-db");
  const old = btn.textContent;
  btn.disabled = true; btn.textContent = "重启中...";
  const st = $("#db-svc-status");
  if (st) { st.textContent = "重启中..."; st.className = "badge running"; }
  try {
    const r = await post("/api/service/restart");
    toast(r.msg || (r.ok ? "重启成功" : "重启失败"), r.ok);
    await loadDbServiceStatus();
  } catch (e) { toast(e.message, false); }
  finally { btn.disabled = false; btn.textContent = old; }
}

/* ---------- 用户管理(新增/授权/改密/删除) ---------- */
const UM_PRIVS = ["SELECT","INSERT","UPDATE","DELETE","CREATE","DROP","ALTER","INDEX",
  "REFERENCES","CREATE VIEW","SHOW VIEW","TRIGGER","EVENT","LOCK TABLES","GRANT OPTION"];
const UM_PRESETS = {
  readonly: ["SELECT"],
  dataentry: ["SELECT","INSERT","UPDATE","DELETE"],
  struct: ["SELECT","INSERT","UPDATE","DELETE","CREATE","ALTER","DROP","INDEX"],
  all: ["ALL PRIVILEGES"],
};
let umMode = "create";
let umTarget = null;

function jsq(s) { return String(s).replace(/'/g, "\\'").replace(/"/g, '\\"'); }

async function loadUserMgmt() {
  try {
    const users = await get("/api/users");
    $("#um-table tbody").innerHTML = users.map((u) => `
      <tr>
        <td class="mono">${esc(u.user)}</td><td class="mono">${esc(u.host)}</td>
        <td>${u.has_password === "YES" ? "已设置" : '<span style="color:var(--text-3)">未设置</span>'}</td>
        <td class="mono">${u.privileges}</td><td class="mono">${esc(u.plugin)}</td>
        <td>${u.locked === "Y" ? '<span class="badge failed">锁定</span>' : '<span class="badge success">正常</span>'}</td>
        <td>
          <button class="btn btn-sm" onclick="window.umViewGrants('${jsq(u.user)}','${jsq(u.host)}')">查看授权</button>
          <button class="btn btn-sm" onclick="window.umEdit('${jsq(u.user)}','${jsq(u.host)}')">设置权限</button>
          <button class="btn btn-sm" onclick="window.umPwd('${jsq(u.user)}','${jsq(u.host)}')">改密</button>
          <button class="btn btn-sm btn-danger" onclick="window.umDel('${jsq(u.user)}','${jsq(u.host)}')">删除</button>
        </td>
      </tr>`).join("") || '<tr><td colspan="7" style="text-align:center;color:var(--text-3);padding:20px">暂无用户</td></tr>';
  } catch (e) { toast(e.message, false); }
}

function renderUmPrivs() {
  $("#um-privs").innerHTML = UM_PRIVS.map((p) => `<label><input type="checkbox" value="${p}"> ${esc(p)}</label>`).join("");
}
function umGetSelectedPrivs() {
  return Array.from(document.querySelectorAll("#um-privs input:checked")).map((c) => c.value);
}
function umSetPrivs(list) {
  const set = new Set(list.map((p) => String(p).trim().toUpperCase()));
  document.querySelectorAll("#um-privs input").forEach((c) => { c.checked = set.has(c.value); });
}
async function umLoadDbs() {
  const dbs = await get("/api/databases");
  $("#um-dbs").innerHTML = dbs.map((d) => `<option value="${esc(d.name)}">${esc(d.name)}</option>`).join("")
    || '<option disabled>无业务数据库</option>';
  return dbs.length;
}

async function openUserModal() {
  umMode = "create"; umTarget = null;
  $("#um-modal-title").textContent = "新增用户";
  $("#um-user").value = ""; $("#um-user").readOnly = false;
  $("#um-host").value = "%";
  $("#um-pwd-field").classList.remove("hidden");
  $("#um-pwd-label").textContent = "密码";
  $("#um-pwd-hint").textContent = "";
  $("#um-pass").value = "";
  $('input[name="um-scope"][value="pick"]').checked = true;
  $("#um-db-wrap").classList.remove("hidden");
  renderUmPrivs(); umSetPrivs([]);
  setStatus($("#um-status"), "");
  try { await umLoadDbs(); } catch (e) {}
  $("#um-modal").classList.remove("hidden");
  $("#um-user").focus();
}
async function openUserGrantsModal(user, host) {
  umMode = "edit"; umTarget = { user, host };
  $("#um-modal-title").textContent = "设置授权 - " + user + "@" + host;
  $("#um-user").value = user; $("#um-user").readOnly = true;
  $("#um-host").value = host;
  $("#um-pwd-field").classList.add("hidden");
  $("#um-pass").value = "";
  $('input[name="um-scope"][value="pick"]').checked = true;
  $("#um-db-wrap").classList.remove("hidden");
  renderUmPrivs(); umSetPrivs([]);
  setStatus($("#um-status"), "");
  try { await umLoadDbs(); } catch (e) {}
  $("#um-modal").classList.remove("hidden");
}
async function umSave() {
  const user = $("#um-user").value.trim();
  const host = $("#um-host").value.trim() || "%";
  const scope_all = $('input[name="um-scope"]:checked').value === "all";
  const dbs = Array.from($("#um-dbs").selectedOptions).map((o) => o.value);
  const privs = umGetSelectedPrivs();
  if (umMode === "create") {
    const pass = $("#um-pass").value;
    if (!user) return setStatus($("#um-status"), "请输入用户名", "err");
    if (!pass) return setStatus($("#um-status"), "请设置密码", "err");
    if (!scope_all && !dbs.length) return setStatus($("#um-status"), "请选择授权数据库或「全部数据库」", "err");
    try {
      await post("/api/users", { user, host, password: pass, scope_all, databases: scope_all ? [] : dbs, privileges: privs });
      $("#um-modal").classList.add("hidden");
      toast("已创建用户 " + user);
      loadUserMgmt();
    } catch (e) { setStatus($("#um-status"), e.message, "err"); }
  } else {
    if (!scope_all && !dbs.length) return setStatus($("#um-status"), "请选择授权数据库或「全部数据库」", "err");
    try {
      await put("/api/users/" + encodeURIComponent(umTarget.user + "@" + umTarget.host),
        { scope_all, databases: scope_all ? [] : dbs, privileges: privs });
      $("#um-modal").classList.add("hidden");
      toast("授权已更新");
      loadUserMgmt();
    } catch (e) { setStatus($("#um-status"), e.message, "err"); }
  }
}

/* 改密 */
let pwdTarget = null;
function openPwdModal(user, host) {
  pwdTarget = { user, host };
  $("#um-pwd-user").value = user + "@" + host;
  $("#um-pwd-pass").value = ""; $("#um-pwd-pass2").value = "";
  setStatus($("#um-pwd-status"), "");
  $("#um-pwd-modal").classList.remove("hidden");
}
async function umPwdSave() {
  const p = $("#um-pwd-pass").value, p2 = $("#um-pwd-pass2").value;
  if (!p || p.length < 6) return setStatus($("#um-pwd-status"), "新密码至少 6 位", "err");
  if (p !== p2) return setStatus($("#um-pwd-status"), "两次输入不一致", "err");
  try {
    await put("/api/users/" + encodeURIComponent(pwdTarget.user + "@" + pwdTarget.host), { password: p });
    $("#um-pwd-modal").classList.add("hidden");
    toast("密码已修改");
  } catch (e) { setStatus($("#um-pwd-status"), e.message, "err"); }
}

/* 查看授权 */
async function viewGrants(user, host) {
  try {
    const r = await get("/api/users/" + encodeURIComponent(user + "@" + host) + "/grants");
    $("#um-grants-title").textContent = "授权详情 - " + user + "@" + host;
    $("#um-grants-body").textContent = (r.grants || []).join("\n") || "该用户暂无权限";
    $("#um-grants-modal").classList.remove("hidden");
  } catch (e) { toast(e.message, false); }
}

/* 删除用户 */
async function deleteUser(user, host) {
  if (!(await confirmDialog("删除用户",
      "确定删除用户 <b>" + esc(user) + "@" + esc(host) + "</b> 吗？其全部权限将一并撤销，此操作不可逆。"))) return;
  try {
    await del("/api/users/" + encodeURIComponent(user + "@" + host));
    toast("已删除用户 " + user);
    loadUserMgmt();
  } catch (e) { toast(e.message, false); }
}

/* 导出到 window(供内联 onclick) */
window.umViewGrants = viewGrants;
window.umEdit = openUserGrantsModal;
window.umPwd = openPwdModal;
window.umDel = deleteUser;

/* 事件绑定 */
$("#btn-new-user").onclick = openUserModal;
$("#btn-restart-db").onclick = restartDb;
$("#um-cancel").onclick = () => $("#um-modal").classList.add("hidden");
$("#um-save").onclick = umSave;
$("#um-pwd-cancel").onclick = () => $("#um-pwd-modal").classList.add("hidden");
$("#um-pwd-save").onclick = umPwdSave;
$("#um-grants-close").onclick = () => $("#um-grants-modal").classList.add("hidden");
$("#um-presets").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-preset]");
  if (!btn) return;
  const p = btn.dataset.preset;
  if (p) umSetPrivs(UM_PRESETS[p]); else umSetPrivs([]);
});
$$('input[name="um-scope"]').forEach((r) => r.onchange = () => {
  $("#um-db-wrap").classList.toggle("hidden", r.value !== "pick");
});

/* ---------- 备份与还原 ---------- */
let browserMode = "backup";

$$('input[name="bk-scope"]').forEach((r) => r.onchange = (e) => {
  $("#bk-db-pick-row").classList.toggle("hidden", e.target.value !== "pick");
});

let _defaultBackupDir = "";
async function loadBackupPage() {
  try {
    const s = await get("/api/settings");
    _defaultBackupDir = (s && s.backup_dir) || "";
    const bk = $("#bk-dir"); if (bk && !bk.value.trim()) bk.value = _defaultBackupDir;
  } catch (e) {}
  await Promise.all([loadBackupDbs(), loadHistory(), loadBackupFiles()]);
}

async function loadBackupDbs() {
  const tip = '<option value="">(加载数据库失败,请先在连接管理激活连接)</option>';
  try {
    const dbs = await get("/api/databases");
    if (!dbs || !dbs.length) {
      $("#bk-db-pick").innerHTML = '<option value="">(暂无业务数据库)</option>';
      return;
    }
    const opts = dbs.map((d) => `<option value="${esc(d.name)}">${esc(d.name)}</option>`).join("");
    $("#bk-db-pick").innerHTML = opts;
  } catch (e) {
    $("#bk-db-pick").innerHTML = tip;
    toast(e.message, false);
  }
}

async function loadHistory() {
  try {
    const list = await get("/api/backups");
    $("#history-table tbody").innerHTML = list.slice().reverse().map((h) => `
      <tr>
        <td>${esc(h.time)}</td>
        <td><span class="badge ${h.type === "backup" ? "backup" : "restore"}">${h.type === "backup" ? "备份" : "还原"}</span></td>
        <td class="mono">${esc(h.host || "-")}</td>
        <td class="mono">${esc((h.dbs || []).join(", "))}</td>
        <td class="ellipsis mono" title="${esc(h.path)}">${esc(h.path)}</td>
        <td class="mono">${fmtSize(h.size)} ${h.compressed ? '<span class="badge">GZ</span>' : ""}</td><td class="mono">${h.elapsed}s</td>
        <td>${h.result === "success" ? '<span class="badge success">成功</span>' : `<span class="badge failed">失败</span>`}${h.warning ? ` <span class="badge running" title="${esc(h.warning)}">⚠</span>` : ""}</td>
        <td>${h.type === "backup" && h.result === "success" && h.exists ? `<button class="btn btn-sm" data-path="${esc(h.path)}" onclick="window.downloadBackup(this)">下载</button> ` : ""}${h.result !== "success" && h.error ? `<button class="btn btn-sm" data-err="${esc(h.error)}" onclick="window.showErr(this)">错误</button>` : (h.warning ? `<button class="btn btn-sm" data-err="${esc(h.warning)}" onclick="window.showErr(this)">警告</button>` : "")}</td>
      </tr>`).join("") || '<tr><td colspan="9" style="text-align:center;color:var(--text-3);padding:20px">暂无备份/还原记录</td></tr>';
  } catch (e) { toast(e.message, false); }
}
$("#btn-refresh-history").onclick = loadHistory;
window.showErr = function (el) { toast("备份失败: " + el.dataset.err, false); };

window.downloadBackup = async function (el) {
  const path = el.dataset.path;
  if (!path) { toast("缺少文件路径", false); return; }
  try {
    const token = localStorage.getItem("mc_token");
    const res = await fetch("/api/backup-files/download?file=" + encodeURIComponent(path), {
      headers: token ? { "Authorization": "Bearer " + token } : {}
    });
    if (!res.ok) {
      let msg = "HTTP " + res.status;
      try { const d = await res.json(); if (d && d.error) msg = d.error; } catch (e) {}
      throw new Error(msg);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = decodeURIComponent(path).split(/[\\/]/).pop();
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  } catch (e) { toast("下载失败: " + e.message, false); }
};
async function loadBackupFiles() {
  try {
    const list = await get("/api/backup-files");
    const tbl = $("#backup-files-table"), empty = $("#backup-files-empty");
    if (!list || !list.length) {
      tbl.classList.add("hidden"); empty.classList.remove("hidden");
      $("#bf-dir-hint").textContent = "";
      return;
    }
    $("#bf-dir-hint").textContent = "共 " + list.length + " 个备份文件";
    empty.classList.add("hidden");
    tbl.classList.remove("hidden");
    $("#backup-files-table tbody").innerHTML = list.map((f) => `
      <tr>
        <td class="ellipsis mono" title="${esc(f.path)}">${esc(f.name)} ${f.compressed ? '<span class="badge">GZ</span>' : ""}</td>
        <td class="mono">${fmtSize(f.size)}</td>
        <td class="mono">${f.mtime ? new Date(f.mtime * 1000).toLocaleString() : "-"}</td>
        <td><button class="btn btn-sm" data-path="${esc(f.path)}" onclick="window.downloadBackup(this)">下载</button></td>
      </tr>`).join("");
  } catch (e) { toast(e.message, false); }
}
$("#btn-refresh-files").onclick = loadBackupFiles;



/* ---------- 进度弹窗 ---------- */
let pmTimer = null;
function showProgressModal(title) {
  $("#pm-title").textContent = title;
  $("#pm-bar").style.width = "0%";
  $("#pm-bar").style.background = "";
  $("#pm-percent").textContent = "0%";
  $("#pm-msg").textContent = "准备中...";
  $("#pm-close").classList.add("hidden");
  $("#pm-close").textContent = "完成";
  $("#progress-modal").classList.remove("hidden");
}
function pollTask(tid, onDone) {
  clearInterval(pmTimer);
  const closeModal = () => {
    clearInterval(pmTimer);
    $("#progress-modal").classList.add("hidden");
    $("#pm-bar").style.background = "";
    loadHistory();
    if (onDone) onDone();
  };
  pmTimer = setInterval(async () => {
    let t = null;
    try { t = await get("/api/task/" + tid); }
    catch (e) {
      clearInterval(pmTimer);
      $("#pm-title").textContent = "操作失败";
      $("#pm-bar").style.background = "#a32d2d";
      $("#pm-msg").textContent = "查询进度失败: " + e.message;
      $("#pm-close").classList.remove("hidden");
      $("#pm-close").onclick = closeModal;
      return;
    }
    const pct = Math.min(100, Math.round(t.percent || 0));
    $("#pm-bar").style.width = pct + "%";
    $("#pm-percent").textContent = pct + "%";
    $("#pm-msg").textContent = `${t.phase} | ${t.message || ""} | 耗时 ${t.elapsed || 0}s`;
    if (t.status === "done" || t.status === "failed") {
      clearInterval(pmTimer);
      const ok = t.status === "done" && t.result && t.result.result === "success";
      $("#pm-title").textContent = ok ? "✅ 操作完成" : "❌ 操作失败";
      $("#pm-bar").style.background = ok ? "#3b6d11" : "#a32d2d";
      if (!ok && t.error) {
        $("#pm-msg").textContent = "错误: " + t.error;
      } else {
        const r = t.result || {};
        const extra = [];
        if (r.size) extra.push("大小 " + fmtSize(r.size));
        if (r.elapsed) extra.push("耗时 " + r.elapsed + "s");
        if (r.path) extra.push(r.path);
        $("#pm-msg").textContent =
          (ok ? "备份/还原已成功完成" : "操作失败") +
          (extra.length ? " — " + extra.join(", ") : "");
      }
      const btn = $("#pm-close");
      btn.textContent = ok ? "完成" : "关闭";
      btn.classList.remove("hidden");
      btn.onclick = closeModal;
    }
  }, 500);
}

$("#btn-backup").onclick = async () => {
  const scope = document.querySelector('input[name="bk-scope"]:checked').value;
  const dbs = scope === "pick" ? [...$("#bk-db-pick").selectedOptions].map((o) => o.value) : [];
  const dir = $("#bk-dir").value.trim();
  const gzip = $("#bk-gzip").checked;
  try {
    const r = await post("/api/backup", { dbs, backup_dir: dir, gzip });
    if (!r.task_id) throw new Error("未返回任务 ID");
    showProgressModal("备份执行中");
    pollTask(r.task_id);
  } catch (e) { toast("备份启动失败: " + e.message, false); }
};

$("#btn-restore").onclick = async () => {
  const target = $("#rs-target-db").value;
  const file = $("#rs-file").value.trim();
  if (!file) { toast("请先选择还原文件", false); return; }
  const ok = await confirmDialog("执行还原",
    `目标数据库: <b>${esc(target || "(使用文件自带建库)")}</b><br>还原文件: <b>${esc(file)}</b><br><br>此操作将覆盖目标库中的同名表,<span style="color:var(--danger)">且不可撤销</span>。建议先执行备份。`);
  if (!ok) return;
  try {
    const r = await post("/api/restore", { target_db: target, file });
    if (!r.task_id) throw new Error("未返回任务 ID");
    showProgressModal("还原执行中");
    pollTask(r.task_id);
  } catch (e) { toast("还原启动失败: " + e.message, false); }
};

/* 目标数据库选择弹窗 */
let _dbListCache = [];
function renderDbPicker() {
  const q = $("#db-picker-filter").value.trim().toLowerCase();
  const list = $("#db-picker-list");
  if (!_dbListCache || !_dbListCache.length) {
    list.innerHTML = '<div class="db-picker-empty">(暂无业务数据库,可直接在「或还原到新库」中输入库名)</div>';
    return;
  }
  const rows = _dbListCache
    .filter((d) => !q || d.name.toLowerCase().includes(q))
    .map((d) => `<div class="db-picker-row" data-db="${esc(d.name)}">
      <span class="db-picker-name mono">${esc(d.name)}</span>
      <span class="db-picker-meta">${d.table_count} 张表 · ${fmtSize(d.total_size)}</span>
    </div>`).join("");
  list.innerHTML = rows || '<div class="db-picker-empty">无匹配的数据库</div>';
  list.querySelectorAll(".db-picker-row").forEach((row) => {
    row.onclick = () => {
      $("#rs-target-db").value = row.dataset.db;
      closeDbPicker();
    };
  });
}
function closeDbPicker() { $("#db-picker-modal").classList.add("hidden"); }
async function openDbPicker() {
  const list = $("#db-picker-list");
  $("#db-picker-filter").value = "";
  $("#db-picker-new-name").value = "";
  list.innerHTML = '<div class="db-picker-empty">正在加载数据库列表...</div>';
  $("#db-picker-modal").classList.remove("hidden");
  try { _dbListCache = await get("/api/databases"); } catch (e) { _dbListCache = []; }
  renderDbPicker();
}
$("#btn-pick-db").onclick = openDbPicker;
$("#db-picker-filter").oninput = renderDbPicker;
$("#db-picker-apply-new").onclick = () => {
  const n = $("#db-picker-new-name").value.trim();
  if (!n) { toast("请先输入新库名", false); return; }
  $("#rs-target-db").value = n;
  closeDbPicker();
};
$("#db-picker-clear").onclick = () => { $("#rs-target-db").value = ""; closeDbPicker(); };
$("#db-picker-cancel").onclick = closeDbPicker;

/* 目录浏览 */
async function openBrowser(mode, startPath, container, onPick) {
  browserMode = mode;
  container.classList.remove("hidden");
  await browseTo(startPath || "", container, onPick);
}
async function browseTo(path, container, onPick) {
  try {
    const d = await post("/api/browse", { path });
    const isBackupMode = browserMode === "backup";
    const box = container.id;
    let html = `<div class="b-head">${esc(d.path || "选择磁盘")}` +
      (d.parent && !d.is_root ? ` <a class="b-link" data-path="${esc(d.parent)}" onclick="window.bNav(this,'${box}')">[上级]</a>` : "") +
      (isBackupMode && d.path ? ` <a class="b-link" data-path="${esc(d.path)}" onclick="window.bPickDir(this)">[选此目录]</a>` : "") +
      `</div>`;
    html += (d.dirs || []).map((x) => `<div class="b-item dir" data-path="${esc(x.path)}" onclick="window.bNav(this,'${box}')">${esc(x.name)}</div>`).join("");
    html += (d.files || []).map((f) => `<div class="b-item file" data-path="${esc(f.path)}" onclick="window.bPickFile(this)">${esc(f.name)}<span class="b-size">${fmtSize(f.size)}</span></div>`).join("");
    if (!(d.dirs || []).length && !(d.files || []).length) html += '<div style="padding:10px;color:var(--text-3);font-size:13px">(空目录)</div>';
    container.innerHTML = html;
  } catch (e) { container.innerHTML = `<div class="b-head">${esc(e.message)}</div>`; }
}
window.bNav = function (el, box) {
  browseTo(el.dataset.path, document.getElementById(box));
};
window.bPickDir = function (el) { pickDir(el.dataset.path); };
window.bPickFile = function (el) { pickFile(el.dataset.path); };
function pickDir(p) { $("#bk-dir").value = p; $("#bk-dir-browser").classList.add("hidden"); }
function pickFile(p) { $("#rs-file").value = p; $("#rs-file-browser").classList.add("hidden"); }
$("#btn-browse-backup").onclick = () => openBrowser("backup", $("#bk-dir").value || "", $("#bk-dir-browser"));
$("#btn-browse-restore").onclick = () => openBrowser("restore", $("#rs-file").value || _defaultBackupDir, $("#rs-file-browser"));

/* 原生文件/目录选择(调用 Windows 对话框) */
/* 通用:调原生对话框选一个目录并回填到输入框(备份目录/客户端目录共用) */
async function pickDirInto(inputSel, title) {
  try {
    const r = await post("/api/dialog", { mode: "dir", title, start_dir: $(inputSel).value.trim() });
    if (r.path) {
      $(inputSel).value = r.path;
      // 客户端目录回填后自动验证一次,省一次点击
      if (inputSel === "#su-mysql-bin") suProbe();
      else if (inputSel === "#set-mysql-bin") probeClientBin();
      $("#bk-dir-browser").classList.add("hidden");
    }
    // r.canceled = 用户主动取消,静默不提示
  } catch (e) { toast("选择失败: " + e.message, false); }
}
$("#btn-pick-dir").onclick = () => pickDirInto("#bk-dir", "选择备份目录");
$("#btn-pick-file").onclick = async () => {
  try {
    const r = await post("/api/dialog", { mode: "file", title: "选择还原文件", start_dir: $("#rs-file").value.trim() || _defaultBackupDir });
    if (r.path) { $("#rs-file").value = r.path; $("#rs-file-browser").classList.add("hidden"); }
  } catch (e) { toast("选择失败: " + e.message, false); }
};

/* ---------- 定时备份(多任务) ---------- */
let scEnv = null;
let scEditingId = null;

async function loadSchedule() {
  try {
    if (!scEnv) {
      scEnv = await get("/api/schedules/env");
      $("#sc-env-tip").textContent =
        `当前系统: ${scEnv.os_desc}` +
        (scEnv.native_available ? ` | 可用系统调度: ${scEnv.native_engine}` : " | 暂不支持系统计划任务,仅可使用内置调度器");
      $("#sf-native-hint").textContent = scEnv.native_available ? `(${scEnv.native_engine})` : "(当前系统不可用)";
    }
    const tasks = await get("/api/schedules");
    const enabledCount = tasks.filter((t) => t.enabled).length;
    $("#sc-summary").textContent = tasks.length ? `共 ${tasks.length} 个任务,${enabledCount} 个启用中` : "";
    $("#sc-table tbody").innerHTML = tasks.map((t) => {
      const engBadge = t.engine === "native"
        ? `<span class="badge restore">系统计划${t.native_registered ? "·已注册" : "·未注册"}</span>`
        : `<span class="badge backup">内置</span>`;
      const stBadge = t.enabled
        ? `<span class="badge success">启用中</span>`
        : `<span class="badge off">已停用</span>`;
      const lastRes = t.last_result === "success" ? `<span class="badge success">成功</span>`
        : (t.last_result === "failed" ? `<span class="badge failed">失败</span>` : "");
      const scope = t.dbs && t.dbs.length ? `${esc(t.dbs[0])}${t.dbs.length > 1 ? ` 等 ${t.dbs.length} 库` : ""}` : "全部数据库";
      return `<tr>
        <td>${esc(t.name)}</td>
        <td>${esc(t.desc || "")}</td>
        <td>${scope}</td>
        <td>${engBadge}</td>
        <td>${stBadge}</td>
        <td class="mono">${esc(t.last_run || "—")} ${lastRes}</td>
        <td>
          <button class="btn btn-sm" data-sc-toggle="${t.id}" data-on="${t.enabled ? 0 : 1}">${t.enabled ? "停用" : "启用"}</button>
          <button class="btn btn-sm" data-sc-edit="${t.id}">编辑</button>
          <button class="btn btn-sm" data-sc-del="${t.id}" style="color:var(--danger)">删除</button>
        </td>
      </tr>`;
    }).join("") || `<tr><td colspan="7" style="text-align:center;color:var(--text-3);padding:20px;">暂无定时任务,点击右上角「新建任务」创建</td></tr>`;
  } catch (e) { toast(e.message, false); }
}

$("#sc-table").addEventListener("click", async (ev) => {
  const tg = ev.target.closest("button");
  if (!tg) return;
  if (tg.dataset.scToggle !== undefined) {
    try {
      await post("/api/schedules/toggle", { id: tg.dataset.scToggle, enabled: tg.dataset.on === "1" });
      loadSchedule();
    } catch (e) { toast(e.message, false); }
  } else if (tg.dataset.scEdit) {
    openScModal(tg.dataset.scEdit);
  } else if (tg.dataset.scDel) {
    const ok = await confirmDialog("删除任务", `确定删除该定时备份任务?<br>已注册到系统的计划任务将一并移除。`);
    if (!ok) return;
    try {
      await del("/api/schedules/" + tg.dataset.scDel);
      toast("任务已删除");
      loadSchedule();
    } catch (e) { toast(e.message, false); }
  }
});

function setFreqRows(freq) {
  $("#sf-hourly-row").classList.toggle("hidden", freq !== "hourly");
  $("#sf-weekly-row").classList.toggle("hidden", freq !== "weekly");
  $("#sf-monthly-row").classList.toggle("hidden", freq !== "monthly");
  $("#sf-time-row").classList.toggle("hidden", freq === "hourly" || freq === "once");
  $("#sf-once-row").classList.toggle("hidden", freq !== "once");
}
$("#sf-freq").onchange = () => setFreqRows($("#sf-freq").value);

document.querySelectorAll('input[name="sf-scope"]').forEach((r) =>
  r.addEventListener("change", () =>
    $("#sf-db-pick-row").classList.toggle("hidden",
      document.querySelector('input[name="sf-scope"]:checked').value !== "pick")));

async function openScModal(tid) {
  scEditingId = tid || null;
  $("#sc-modal-title").textContent = tid ? "编辑定时备份任务" : "新建定时备份任务";
  $("#sf-name").value = "";
  $("#sf-freq").value = "daily";
  $("#sf-interval").value = 1; $("#sf-weekday").value = 0; $("#sf-day").value = 1;
  $("#sf-time").value = "02:00"; $("#sf-once").value = ""; $("#sf-keep").value = 7;
  document.querySelector('input[name="sf-engine"][value="builtin"]').checked = true;
  document.querySelector('input[name="sf-scope"][value="all"]').checked = true;
  $("#sf-db-pick-row").classList.add("hidden");
  setFreqRows("daily");
  try {
    const dbs = await get("/api/databases");
    $("#sf-db-pick").innerHTML = dbs.map((d) => `<option value="${esc(d.name)}">${esc(d.name)}</option>`).join("");
  } catch (e) { /* 未激活连接时忽略 */ }
  if (tid) {
    try {
      const tasks = await get("/api/schedules");
      const t = tasks.find((x) => x.id === tid);
      if (t) {
        $("#sf-name").value = t.name;
        $("#sf-freq").value = t.freq;
        $("#sf-interval").value = t.interval_hours || 1;
        $("#sf-weekday").value = t.weekday == null ? 0 : t.weekday;
        $("#sf-day").value = t.day_of_month || 1;
        $("#sf-time").value = t.time || "02:00";
        $("#sf-once").value = t.at_once || "";
        $("#sf-keep").value = t.keep || 7;
        document.querySelector(`input[name="sf-engine"][value="${t.engine || "builtin"}"]`).checked = true;
        setFreqRows(t.freq);
        if (t.dbs && t.dbs.length) {
          document.querySelector('input[name="sf-scope"][value="pick"]').checked = true;
          $("#sf-db-pick-row").classList.remove("hidden");
          [...$("#sf-db-pick").options].forEach((o) => { if (t.dbs.includes(o.value)) o.selected = true; });
        }
      }
    } catch (e) { toast(e.message, false); }
  }
  $("#sc-modal").classList.remove("hidden");
}

$("#btn-new-sc").onclick = () => openScModal(null);
$("#sf-cancel").onclick = () => $("#sc-modal").classList.add("hidden");

$("#sf-save").onclick = async () => {
  const payload = {
    name: $("#sf-name").value.trim(),
    freq: $("#sf-freq").value,
    engine: document.querySelector('input[name="sf-engine"]:checked').value,
    interval_hours: parseInt($("#sf-interval").value) || 1,
    weekday: parseInt($("#sf-weekday").value) || 0,
    day_of_month: parseInt($("#sf-day").value) || 1,
    time: $("#sf-time").value || "02:00",
    at_once: $("#sf-once").value,
    keep: parseInt($("#sf-keep").value) || 7,
    dbs: [...$("#sf-db-pick").selectedOptions].map((o) => o.value),
  };
  const scope = document.querySelector('input[name="sf-scope"]:checked').value;
  if (scope !== "pick") payload.dbs = [];
  try {
    let r;
    if (scEditingId) {
      r = await put("/api/schedules/" + scEditingId, payload);
    } else {
      r = await post("/api/schedules", payload);
    }
    const newId = scEditingId || r.id;
    if (payload.engine === "native") {
      try {
        const reg = await post("/api/schedules/register", { id: newId });
        toast(reg.ok ? "任务已保存并注册到系统计划任务" : "任务已保存,但注册失败: " + (reg.error || ""), reg.ok);
      } catch (e) { toast("任务已保存,但注册失败: " + e.message, false); }
    } else {
      // 编辑时若从 native 切回 builtin,后端已自动反注册
      toast(scEditingId ? "任务已更新" : "任务已创建");
    }
    $("#sc-modal").classList.add("hidden");
    loadSchedule();
  } catch (e) { toast(e.message, false); }
};

/* ---------- 日志 ---------- */
async function loadLogs() {
  try {
    const lines = await get("/api/logs");
    $("#log-view").innerHTML = (lines || []).map((l) => {
      const cls = l.includes("FAIL") ? "fail" : (l.includes("OK") ? "ok" : "");
      return `<div class="${cls}">${esc(l)}</div>`;
    }).join("") || "暂无日志";
  } catch (e) { toast(e.message, false); }
}
$("#btn-refresh-logs").onclick = loadLogs;

/* ---------- 服务设置弹窗 ---------- */
async function openSettingsModal() {
  const s = await get("/api/settings");
  $("#set-mysql-bin").value = s.mysql_bin || "";
  $("#set-backup-dir").value = s.backup_dir || "";
  setStatus($("#set-probe-status"), "");
  $("#settings-modal").classList.remove("hidden");
}
$("#btn-show-settings").onclick = () => openSettingsModal().catch((e) => toast(e.message, false));
$("#set-cancel").onclick = () => $("#settings-modal").classList.add("hidden");
$("#btn-pick-client-dir").onclick = () => pickDirInto("#set-mysql-bin", "选择 MySQL 客户端目录");
/* 验证客户端目录:实际执行 mysqldump --version(设置弹窗/向导共用) */
async function probeClientBin() {
  const st = $("#set-probe-status");
  if (!st) return;
  setStatus(st, "验证中...");
  try {
    const r = await post("/api/setup/probe-client", { path: $("#set-mysql-bin").value.trim() });
    if (r.path) $("#set-mysql-bin").value = r.dir;
    setStatus(st, `✓ ${r.version || "可用"} (${r.path})`, "ok");
  } catch (e) { setStatus(st, "✗ " + e.message, "err"); }
}
$("#btn-probe-client").onclick = () => probeClientBin();
$("#set-save").onclick = async () => {
  try {
    await put("/api/settings", {
      mysql_bin: $("#set-mysql-bin").value.trim(),
      backup_dir: $("#set-backup-dir").value.trim(),
    });
    toast("设置已保存");
    $("#settings-modal").classList.add("hidden");
  } catch (e) { toast(e.message, false); }
};
$("#btn-change-password").onclick = async () => {
  const st = $("#set-pw-status");
  const oldPw = $("#set-old-pass").value;
  const newPw = $("#set-new-pass").value;
  const newPw2 = $("#set-new-pass2").value;
  if (!oldPw || !newPw || !newPw2) { setStatus(st, "请填写完整", "err"); return; }
  if (newPw.length < 6) { setStatus(st, "新密码至少 6 位", "err"); return; }
  if (newPw !== newPw2) { setStatus(st, "两次输入的新密码不一致", "err"); return; }
  setStatus(st, "修改中...");
  try {
    await post("/api/change-password", { old_password: oldPw, new_password: newPw });
    setStatus(st, "密码修改成功", "ok");
    $("#set-old-pass").value = "";
    $("#set-new-pass").value = "";
    $("#set-new-pass2").value = "";
  } catch (e) { setStatus(st, e.message, "err"); }
};

/* ---------- 首次部署三步引导 ---------- */
const suState = { step: 1, env: null, dbOk: false };
// 向导步骤:1 环境检测 → 2 客户端与目录 → 3 运行模式 → 4 数据库连接
const SU_PANE_FOR = { 1: "su-pane-1", 2: "su-pane-2", 3: "su-pane-mode", 4: "su-pane-4" };
const SU_STEPS = [1, 2, 3, 4];
function suGoto(n) {
  suState.step = n;
  SU_STEPS.forEach((i) => {
    document.getElementById(SU_PANE_FOR[i]).classList.toggle("hidden", i !== n);
    const badge = document.querySelector(`.s-step[data-step="${i}"]`);
    if (badge) {
      badge.classList.toggle("active", i === n);
      badge.classList.toggle("done", i < n);
    }
  });
  $("#su-prev").classList.toggle("hidden", n === 1);
  $("#su-next").textContent = n === 4 ? "完成配置" : "下一步";
}
window.selectMode = function(mode) {
  suState.runMode = mode;
  document.querySelectorAll(".mode-card").forEach((c) => {
    c.classList.toggle("selected", c.dataset.mode === mode);
  });
  var el = document.getElementById("mode-full-options");
  if (el) el.style.display = mode === "full" ? "" : "none";
};
selectMode("full");

async function openSetup(force) {
  let s = {};
  try { s = await get("/api/settings"); } catch (e) {}
  if (!force && s.setup_done) return false;
  // 预填已有配置(重跑引导场景)
  try {
    const env0 = await get("/api/setup/env");
    $("#su-mysql-bin").value = env0.mysql_bin_found || "";
  } catch (e) {}
  try {
    const st = await get("/api/settings");
    if (!$("#su-mysql-bin").value) $("#su-mysql-bin").value = "";
    $("#su-backup-dir").value = st.backup_dir || "";
  } catch (e) {}
  $("#setup-modal").classList.remove("hidden");
  suGoto(1);
  runEnvCheck();
  return true;
}
async function runEnvCheck() {
  const tb = $("#su-env-table tbody");
  tb.innerHTML = '<tr><td style="padding:14px;color:var(--text-3)">检测中...</td></tr>';
  try {
    const env = await get("/api/setup/env");
    suState.env = env;
    tb.innerHTML = env.items.map((it) => `
      <tr>
        <td style="width:34px;">${it.ok ? '✅' : (env.items.slice(0,3).includes(it) ? '❌' : '⚠️')}</td>
        <td><b>${esc(it.name)}</b>${it.detail ? `<div class="hint">${esc(it.detail)}</div>` : ""}</td>
        <td class="hint">${it.ok ? "" : esc(it.tip)}</td>
      </tr>`).join("");
    $("#su-env-summary").textContent = env.all_required_ok
      ? "核心依赖齐备,可继续。MySQL 客户端缺失只影响备份/还原,可在下一步配置。"
      : "存在缺失项。Python/PyMySQL 缺失需在服务器端修复;客户端缺失可下一步手动指定。";
  } catch (e) {
    tb.innerHTML = `<tr><td style="padding:14px;color:var(--danger)">检测失败: ${esc(e.message)}</td></tr>`;
  }
}
$("#su-btn-recheck").onclick = async () => {
  setStatus($("#su-probe-status"), "");
  $("#su-cli-warn").classList.add("hidden");
  try {
    const env = await get("/api/setup/env");
    $("#su-mysql-bin").value = env.mysql_bin_found || $("#su-mysql-bin").value;
    setStatus($("#su-probe-status"), env.mysqldump_path ? `已找到: ${env.mysqldump_path}` : "仍未找到,请手动填写目录后点「验证」",
              env.mysqldump_path ? "ok" : "");
    $("#su-cli-warn").classList.toggle("hidden", !!env.mysqldump_path);
  } catch (e) { setStatus($("#su-probe-status"), e.message, "err"); }
};
/* 向导第 2 步「选择目录」:调 Windows 原生对话框,选中后自动验证 */
$("#su-btn-pick").onclick = () => pickDirInto("#su-mysql-bin", "选择 MySQL 客户端目录");
/* 向导第 2 步「默认备份目录」:调 Windows 原生目录选择对话框 */
$("#su-btn-pick-backup-dir").onclick = () => pickDirInto("#su-backup-dir", "选择默认备份目录");
/* 向导版验证:结果写入向导状态行(逻辑与设置弹窗一致) */
async function suProbe() {
  const st = $("#su-probe-status");
  if (!st) return;
  setStatus(st, "验证中...");
  try {
    const r = await post("/api/setup/probe-client", { path: $("#su-mysql-bin").value.trim() });
    if (r.dir) $("#su-mysql-bin").value = r.dir;
    setStatus(st, `✓ ${r.version || "可用"} (${r.path})`, "ok");
    $("#su-cli-warn").classList.add("hidden");
  } catch (e) {
    setStatus(st, "✗ " + e.message, "err");
    $("#su-cli-warn").classList.remove("hidden");
  }
}
$("#su-btn-probe").onclick = () => suProbe();
$("#su-btn-testdb").onclick = async () => {
  const st = $("#su-db-status");
  setStatus(st, "连接中...");
  const cfg = {
    host: $("#su-cf-host").value.trim() || "127.0.0.1",
    port: parseInt($("#su-cf-port").value) || 3306,
    user: $("#su-cf-user").value.trim() || "root",
    password: $("#su-cf-pass").value,
  };
  try {
    const r = await post("/api/setup/test-db", cfg);
    suState.dbOk = !!r.ok;
    setStatus(st, r.ok ? `✓ 连接成功,服务器版本 ${r.version}` : `✗ ${r.error}`, r.ok ? "ok" : "err");
    if (r.ok) suRenderSysDbWarn(await suCheckSysDb());
  } catch (e) { suState.dbOk = false; setStatus(st, "✗ " + e.message, "err"); }
};
/* Bug5: 检测旧/同名系统库,渲染"警告 + 可选删除" */
async function suCheckSysDb() {
  const cfg = {
    host: $("#su-cf-host").value.trim() || "127.0.0.1",
    port: parseInt($("#su-cf-port").value) || 3306,
    user: $("#su-cf-user").value.trim() || "root",
    password: $("#su-cf-pass").value,
  };
  suState.dbCfg = cfg;
  const target_db = $("#mc-sys-db").value.trim() || "_mysql_console";
  try {
    const r = await post("/api/setup/db-check", Object.assign({ target_db }, cfg));
    return Object.assign({ target_db }, r || {});
  } catch (e) { return { target_db }; }
}
function suRenderSysDbWarn(res) {
  const box = $("#su-sysdb-warn");
  const txt = $("#su-sysdb-warn-text");
  const btns = $("#su-sysdb-warn-btns");
  box.classList.add("hidden");
  txt.innerHTML = ""; btns.innerHTML = "";
  if (!res) return;
  const items = [];
  if (res.target_exists) items.push({ name: res.target_db, type: "同名库(将复用其现有数据)" });
  (res.legacy_dbs || []).forEach((l) => {
    if (l.o_exists || l.exists) items.push({ name: l.name, type: "旧系统库(遗留,切走后未用)" });
  });
  if (!items.length) return;
  txt.innerHTML = items.map((it) => `<div>⚠ 检测到<b>${esc(it.name)}</b>(${esc(it.type)})。确认无需其数据可删除后重建。</div>`).join("");
  btns.innerHTML = items.map((it) => `<button class="btn btn-sm btn-danger" data-db="${esc(it.name)}">删除 ${esc(it.name)}</button>`).join("");
  btns.querySelectorAll("[data-db]").forEach((b) => {
    b.onclick = async () => {
      const go = await confirmDialog("删除系统库",
        `将执行 DROP DATABASE \`${esc(b.dataset.db)}\`,该库内数据将被<strong>永久删除</strong>。确定删除吗?`);
      if (!go) return;
      try {
        const r = await post("/api/setup/drop-db", Object.assign({ db_name: b.dataset.db }, suState.dbCfg || {}));
        if (r.ok) {
          setStatus($("#su-db-status"), `已删除 ${b.dataset.db},可重新初始化`, "ok");
          suRenderSysDbWarn(await suCheckSysDb());
        } else {
          setStatus($("#su-db-status"), `删除失败: ${r.error || "未知错误"}`, "err");
        }
      } catch (e) { toast("删除失败: " + e.message, false); }
    };
  });
  box.classList.remove("hidden");
}
$("#su-prev").onclick = () => suGoto(Math.max(1, suState.step - 1));
$("#su-next").onclick = async () => {
  try {
    if (suState.step === 2 && !$("#su-cli-warn").classList.contains("hidden")
        && !$("#su-mysql-bin").value.trim()) {
      const go = await confirmDialog("跳过客户端配置",
        "尚未提供 MySQL 客户端目录,备份/还原功能将不可用(监控不受影响)。<br>确定跳过吗?");
      if (!go) return;
    }
    if (suState.step === 3) {
      const mode = suState.runMode || "full";
      if (mode === "full") {
        const sysDb = $("#mc-sys-db").value.trim() || "_mysql_console";
        const adminUser = $("#mc-admin-user").value.trim() || "admin";
        const adminPass = $("#mc-admin-pass").value;
        const adminPass2 = $("#mc-admin-pass2").value;
        if (adminPass.length < 6) { toast("管理员密码至少 6 位", false); return; }
        if (adminPass !== adminPass2) { toast("两次输入的密码不一致", false); return; }
        suState.sysDbName = sysDb;
        suState.adminUser = adminUser;
        suState.adminPass = adminPass;
      }
      suGoto(4);
      return;
    }
    if (suState.step < 3) { suGoto(suState.step + 1); return; }
    // 第 4 步(数据库连接)→ 完成
    const conn = {
      name: $("#su-cf-name").value.trim() || `MySQL(${($("#su-cf-host").value.trim() || "127.0.0.1")})`,
      host: $("#su-cf-host").value.trim() || "127.0.0.1",
      port: parseInt($("#su-cf-port").value) || 3306,
      user: $("#su-cf-user").value.trim() || "root",
      password: $("#su-cf-pass").value,
      note: "首次部署引导创建",
    };
    if (!conn.password && suState.dbOk !== true) {
      const go = await confirmDialog("未测试连接", "尚未成功测试过数据库连接,确定仍要保存吗?<br>(密码为空时建议先点「测试连接」)");
      if (!go) return;
    }
    const payload = {
      mysql_bin: $("#su-mysql-bin").value.trim(),
      backup_dir: $("#su-backup-dir").value.trim(),
      conn,
      run_mode: suState.runMode || "lite",
    };
    if (payload.run_mode === "full") {
      payload.sys_db_name = suState.sysDbName || "_mysql_console";
      payload.admin_user = suState.adminUser || "admin";
      payload.admin_pass = suState.adminPass || "";
    }
    await post("/api/setup/finish", payload);
    $("#setup-modal").classList.add("hidden");
    toast("初始化配置已完成,正在进入...");
    location.reload();  // 重新以"已配置"状态初始化,正确进入
    return;
  } catch (e) { toast("保存失败: " + e.message, false); }
};
$("#btn-rerun-setup").onclick = () => openSetup(true);

/* ---------- 数据看板 ---------- */
async function loadDashboardPage() {
  // 健康评分
  try {
    const h = await get("/api/dashboard/health");
    const scoreEl = $("#health-score");
    scoreEl.textContent = h.score;
    scoreEl.className = "health-score " + (h.score >= 75 ? "good" : h.score >= 60 ? "warn" : "bad");
    const ring = $("#health-ring");
    if (ring) ring.style.setProperty("--p", h.score);
    $("#health-label").textContent = h.label;
    $("#health-items").innerHTML = h.items.map((it) =>
      `<div class="health-item"><div class="health-item-val" style="color:${it.ok ? "var(--success)" : "var(--danger)"}">${it.value}</div><div class="health-item-lbl">${it.label}</div></div>`
    ).join("");
  } catch (e) {}
  // InnoDB
  try {
    const m = await get("/api/dashboard/innodb");
    $("#innodb-hit-rate").textContent = m.hit_rate;
    $("#innodb-rows-read").textContent = fmtNum(m.rows_read);
    $("#innodb-rows-write").textContent = fmtNum(m.rows_inserted + m.rows_updated + m.rows_deleted);
    $("#innodb-lock-waits").textContent = fmtNum(m.lock_waits);
  } catch (e) {}
  // 表空间
  try {
    const ts = await get("/api/dashboard/tablespace");
    const list = $("#tablespace-list");
    list.innerHTML = ts.length ? ts.map((t) =>
      `<div class="tablespace-row">
        <span class="tablespace-name">${esc(t.db)}.${esc(t.name)}</span>
        <span class="tablespace-size">${fmtSize(t.total_size)} · ${fmtNum(t.rows)}行</span>
      </div>`
    ).join("") : '<div class="hint" style="padding:12px;text-align:center;">暂无数据</div>';
  } catch (e) {}
  // 复制
  try {
    const r = await get("/api/dashboard/replication");
    const el = $("#replication-status");
    if (!r.is_slave) {
      el.innerHTML = `<div class="hint" style="color:var(--text-3);">🖥️ ${esc(r.message)}</div>`;
    } else {
      const ioOk = r.io_running === "Yes";
      const sqlOk = r.sql_running === "Yes";
      el.innerHTML = `
        <div class="info-row"><span class="info-label">IO 线程</span><span class="info-value ${ioOk ? "ok" : "err"}">${ioOk ? "✅ 运行中" : "❌ 停止"}</span></div>
        <div class="info-row"><span class="info-label">SQL 线程</span><span class="info-value ${sqlOk ? "ok" : "err"}">${sqlOk ? "✅ 运行中" : "❌ 停止"}</span></div>
        <div class="info-row"><span class="info-label">主库</span><span class="info-value">${esc(r.master_host)}</span></div>
        <div class="info-row"><span class="info-label">延迟</span><span class="info-value ${r.seconds_behind === "0" ? "ok" : "warn"}">${r.seconds_behind} 秒</span></div>
        ${r.last_error ? `<div class="info-row"><span class="info-label">错误</span><span class="info-value err">${esc(r.last_error)}</span></div>` : ""}`;
    }
  } catch (e) {}
  // 刷新按钮
  const btn = $("#btn-refresh-health");
  if (btn) btn.onclick = loadDashboardPage;
}

function fmtNum(n) {
  if (n >= 1e9) return (n / 1e9).toFixed(1) + "B";
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "K";
  return String(n || 0);
}

/* ---------- 服务器变量 ---------- */
let _allVars = [];
async function loadVariablesPage() {
  try {
    _allVars = await get("/api/variables");
    renderVars(_allVars);
  } catch (e) { toast(e.message, false); }
}
function renderVars(list) {
  const tb = $("#var-table tbody");
  tb.innerHTML = list.map((v) =>
    `<tr><td class="mono" style="font-size:12.5px;">${esc(v.name)}</td><td class="mono" style="font-size:12.5px;word-break:break-all;color:var(--text-2);">${esc(v.value)}</td><td style="font-size:12.5px;color:var(--text-3);">${esc(v.desc || "")}</td></tr>`
  ).join("") || '<tr><td colspan="3" style="text-align:center;padding:20px;" class="hint">暂无数据</td></tr>';
}
$("#var-filter").oninput = (e) => {
  const q = e.target.value.toLowerCase();
  renderVars(_allVars.filter((v) => v.name.toLowerCase().includes(q)));
};
$("#btn-refresh-vars").onclick = loadVariablesPage;

/* ---------- 告警中心 ---------- */
async function loadAlertsPage() {
  try {
    const r = await get("/api/alerts");
    const content = $("#alerts-content");
    if (!r.alerts || r.alerts.length === 0) {
      content.innerHTML = '<div style="padding:20px;text-align:center;color:var(--success);">✅ 当前无告警</div>';
    } else {
      content.innerHTML = r.alerts.map((a) =>
        `<div style="padding:10px 14px;margin-bottom:8px;border-radius:8px;background:${a.level === "critical" ? "var(--danger-bg)" : "var(--warn-bg)"};color:${a.level === "critical" ? "var(--danger)" : "var(--warn)"};">
          <strong>${a.level === "critical" ? "🔴 严重" : "🟡 警告"}</strong> ${esc(a.message)}
        </div>`
      ).join("");
    }
    const btn = $("#btn-refresh-alerts");
    if (btn) btn.onclick = loadAlertsPage;
    // 回填阈值设置
    const s = await get("/api/settings");
    if (s && s.settings) {
      const v = s.settings;
      if (v.alert_max_conn != null) $("#alert-max-conn").value = v.alert_max_conn;
      if (v.alert_max_slow != null) $("#alert-max-slow").value = v.alert_max_slow;
      if (v.alert_max_running != null) $("#alert-max-running").value = v.alert_max_running;
    }
  } catch (e) { toast(e.message, false); }
}
$("#btn-save-alert-settings").onclick = async () => {
  const st = $("#alert-settings-status");
  try {
    const maxConn = parseInt($("#alert-max-conn").value, 10);
    const maxSlow = parseInt($("#alert-max-slow").value, 10);
    const maxRunning = parseInt($("#alert-max-running").value, 10);
    if ([maxConn, maxSlow, maxRunning].some((n) => !Number.isFinite(n) || n < 1)) {
      setStatus(st, "请输入 ≥1 的有效数值", false);
      return;
    }
    await put("/api/settings", {
      alert_max_conn: maxConn,
      alert_max_slow: maxSlow,
      alert_max_running: maxRunning,
    });
    setStatus(st, "阈值已保存", "ok");
    loadAlertsPage();
  } catch (e) { setStatus(st, "保存失败: " + e.message, false); }
};

/* ---------- 系统设置 ---------- */
async function loadSettingsPage() {
  try {
    const s = await get("/api/settings");
    $("#set-username").value = (await get("/api/auth-status")).username || "admin";
    const isFull = s.run_mode === "full";
    const modeText = isFull ? "全量模式" : "轻量模式";
    $("#info-run-mode").textContent = modeText;
    $("#info-run-mode").className = "info-value " + (isFull ? "ok" : "");
    $("#info-sys-db").textContent = s.sys_db_name || "—";
    // 系统库状态
    try {
      await get("/api/databases");
      $("#info-sys-status").textContent = "已连接";
      $("#info-sys-status").className = "info-value ok";
    } catch (e) {
      $("#info-sys-status").textContent = "未连接";
      $("#info-sys-status").className = "info-value err";
    }
    // 登录用户
    const auth = await get("/api/auth-status");
    $("#info-login-user").textContent = auth.username || "—";
    // Token 信息
    const token = localStorage.getItem("mc_token");
    if (token) {
      $("#info-token-expiry").textContent = "8小时有效";
      $("#info-token-expiry").className = "info-value ok";
    } else {
      $("#info-token-expiry").textContent = "—";
    }
    // 模式切换 section: 非全量模式时显示
    const switchSection = document.getElementById("mode-switch-section");
    if (switchSection) switchSection.style.display = isFull ? "none" : "";
  } catch (e) { toast(e.message, false); }
}
$("#btn-save-username").onclick = async () => {
  const st = $("#set-pw-status");
  const username = $("#set-username").value.trim();
  if (!username) { setStatus(st, "请输入用户名", "err"); return; }
  try {
    await put("/api/settings", { admin_username: username });
    // 同步更新 config_store 中的 admin_username
    await post("/api/change-username", { username });
    setStatus(st, "用户名已保存", "ok");
  } catch (e) { setStatus(st, e.message, "err"); }
};
$("#btn-change-password").onclick = async () => {
  const st = $("#set-pw-status");
  const oldPw = $("#set-old-pass").value;
  const newPw = $("#set-new-pass").value;
  const newPw2 = $("#set-new-pass2").value;
  if (!oldPw || !newPw || !newPw2) { setStatus(st, "请填写完整", "err"); return; }
  if (newPw.length < 6) { setStatus(st, "新密码至少 6 位", "err"); return; }
  if (newPw !== newPw2) { setStatus(st, "两次输入的新密码不一致", "err"); return; }
  setStatus(st, "修改中...");
  try {
    await post("/api/change-password", { old_password: oldPw, new_password: newPw });
    setStatus(st, "密码修改成功", "ok");
    $("#set-old-pass").value = "";
    $("#set-new-pass").value = "";
    $("#set-new-pass2").value = "";
  } catch (e) { setStatus(st, e.message, "err"); }
};
$("#btn-goto-reset").onclick = () => {
  localStorage.removeItem("mc_token");
  location.href = "/login.html";
};
$("#btn-switch-full").onclick = async () => {
  const st = $("#switch-status");
  const sysDb = $("#switch-sys-db").value.trim() || "_mysql_console";
  const adminUser = $("#switch-admin-user").value.trim() || "admin";
  const adminPass = $("#switch-admin-pass").value;
  const adminPass2 = $("#switch-admin-pass2").value;
  if (adminPass.length < 6) { setStatus(st, "密码至少 6 位", "err"); return; }
  if (adminPass !== adminPass2) { setStatus(st, "两次输入的密码不一致", "err"); return; }
  if (!confirm("切换到全量模式后不可逆，确认继续？")) return;
  setStatus(st, "切换中...");
  try {
    await post("/api/switch-to-full-mode", { sys_db_name: sysDb, admin_user: adminUser, admin_pass: adminPass });
    setStatus(st, "切换成功，正在刷新...", "ok");
    setTimeout(() => { location.reload(); }, 1000);
  } catch (e) { setStatus(st, e.message, "err"); }
};

/* ---------- 初始化 ---------- */
document.querySelectorAll(".nav-item").forEach((el) => {
  el.onclick = () => switchPage(el.dataset.page);
});
document.querySelectorAll("[data-goto]").forEach((el) => {
  el.onclick = () => switchPage(el.dataset.goto);
});

window.addEventListener("resize", () => {
  if (connChart) connChart.resize();
  if (qpsChart) qpsChart.resize();
});

async function init() {
  initCharts();
  // 检查认证状态
  try {
    const auth = await get("/api/auth-status");
    if (auth.password_set) {
      const token = localStorage.getItem("mc_token");
      if (!token) {
        location.href = "/login.html";
        return;
      }
      // 显示退出登录按钮
      const logoutBtn = document.getElementById("btn-logout");
      if (logoutBtn) logoutBtn.style.display = "";
      // 显示用户名
      if (auth.username) {
        const statusBtn = document.getElementById("conn-status");
        if (statusBtn) statusBtn.title = "当前用户: " + auth.username;
      }
    }
  } catch (e) { /* 健康检查失败也继续 */ }
  // 未初始化(首次安装/重新引导前)→ 显示空白欢迎页,而非概览监控 + 强制弹窗
  let settings = {};
  try { settings = await get("/api/settings") || {}; } catch (e) {}
  if (!settings.setup_done) {
    setupLanding();
    return;
  }
  let conns = [];
  try { conns = await get("/api/connections") || []; } catch (e) {}
  if (conns.length) {
    await loadConnections();
    try {
      const active = conns.find((c) => c.active) || conns[0];
      await post("/api/connect", { id: active.id });
      updateConnStatus(true);
      $("#conn-select").value = active.id;
    } catch (e) { updateConnStatus(false); }
    switchPage("overview");
  } else {
    switchPage("overview");
    updateConnStatus(false);
    openSetup(false).catch(() => {});
  }
}

/* 首次安装/未初始化:后台留白,只显示引导(不渲染概览监控) */
function setupLanding() {
  document.querySelectorAll(".page").forEach((p) => p.classList.add("hidden"));
  const welcome = $("#welcome-banner");
  if (welcome) welcome.classList.remove("hidden");
  const aside = document.querySelector(".sidebar");
  if (aside) aside.style.display = "none";
  const sel = $("#conn-select"); if (sel) sel.style.display = "none";
  const st = $("#conn-status"); if (st) st.style.display = "none";
  const lg = $("#btn-logout"); if (lg) lg.style.display = "none";
  const pt = $("#page-title"); if (pt) pt.textContent = "初始化配置";
  updateConnStatus(false);
  openSetup(true).catch(() => {});
}

/* ---------- 软件更新 ---------- */
async function initUpdateBadge() {
  try {
    const r = await get("/api/update/badge");
    const b = $("#btn-update-badge");
    const iv = $("#info-version");
    const is = $("#info-update-state");
    if (iv && r && r.current) iv.textContent = "v" + r.current;
    if (b && r && r.has_update) {
      b.style.display = "";
      b.onclick = () => switchPage("settings");
      if (is && r.latest) is.textContent = "发现新版本 v" + r.latest;
    } else if (is) {
      is.textContent = (r && r.offline) ? "检查不可用(离线)" : (r && r.latest ? "已是最新 v" + r.latest : "—");
    }
  } catch (e) {}
}
async function loadUpdatePanel() {
  try {
    const s = await get("/api/settings");
    const sel = $("#up-interval");
    if (sel && s && s.update_check_interval) sel.value = s.update_check_interval;
  } catch (e) {}
  try {
    const r = await get("/api/update/badge");
    $("#up-current").textContent = r && r.current ? "v" + r.current : "—";
    $("#up-latest").textContent = r && r.latest ? "v" + r.latest : "—";
    if (r && r.has_update) {
      $("#up-result").textContent = "发现新版本 v" + r.latest;
      $("#up-result").className = "hint";
      $("#up-actions").classList.remove("hidden");
      $("#up-changelog").textContent = (r.body || "").slice(0, 1500);
      $("#up-changelog").classList.remove("hidden");
    } else if (r && r.offline) {
      $("#up-result").textContent = "无法连接 GitHub(离线),无法检查更新";
      $("#up-actions").classList.add("hidden");
    } else {
      $("#up-result").textContent = r && r.latest ? "已是最新版本 v" + r.latest : "—";
      $("#up-actions").classList.add("hidden");
    }
  } catch (e) {}
}
async function checkUpdateNow() {
  const st = $("#up-result");
  st.textContent = "检查中..."; st.className = "hint";
  try {
    const r = await get("/api/update/check");
    if (r.has_update) {
      st.textContent = "发现新版本 v" + r.latest;
      $("#up-latest").textContent = "v" + r.latest;
      $("#up-actions").classList.remove("hidden");
      $("#up-changelog").textContent = (r.body || "").slice(0, 1500);
      $("#up-changelog").classList.remove("hidden");
    } else if (r.offline) {
      st.textContent = "无法连接 GitHub(离线)";
      $("#up-actions").classList.add("hidden");
    } else {
      st.textContent = "已是最新版本 v" + r.latest;
      $("#up-actions").classList.add("hidden");
    }
  } catch (e) { st.textContent = e.message; }
}
async function prepareUpdate() {
  const st = $("#up-status");
  st.textContent = "下载并校验中,请稍候..."; st.className = "";
  try {
    const r = await post("/api/update/prepare");
    st.textContent = r.msg || (r.ok ? "准备完成" : "失败");
    st.className = r.ok ? "ok" : "err";
    if (r.ok) $("#btn-apply-update").classList.remove("disabled");
  } catch (e) { st.textContent = e.message; st.className = "err"; }
}
async function applyUpdate() {
  if (!(await confirmDialog("应用更新",
      "将替换程序代码并重启服务, 期间页面会短暂不可访问(约 30 秒)。确定继续?"))) return;
  try {
    const r = await post("/api/update/apply");
    const st = $("#up-status");
    st.textContent = r.msg || "更新已启动";
    st.className = "ok";
    setTimeout(() => toast("正在更新, 请稍后刷新页面..."), 2000);
  } catch (e) { $("#up-status").textContent = e.message; }
}
async function saveUpInterval() {
  try {
    await put("/api/settings", { update_check_interval: $("#up-interval").value });
    setStatus($("#up-save-status"), "已保存", "ok");
  } catch (e) { setStatus($("#up-save-status"), e.message, "err"); }
}
$("#btn-check-update").onclick = checkUpdateNow;
$("#btn-prepare-update").onclick = prepareUpdate;
$("#btn-apply-update").onclick = applyUpdate;
$("#btn-save-up-interval").onclick = saveUpInterval;

init();
initUpdateBadge();

// ---------- 认证相关 ----------
async function logout() {
  try { await post("/api/logout"); } catch (e) {}
  localStorage.removeItem("mc_token");
  location.href = "/login.html";
}

async function changePassword(oldPw, newPw) {
  await post("/api/change-password", { old_password: oldPw, new_password: newPw });
}

// 暴露到全局供 login.html 调用
window.mcLogout = logout;
window.mcChangePassword = changePassword;
