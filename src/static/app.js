/* MySQL Console 前端逻辑
 * 单文件零构建(与后端零框架一致),普通 <script> 加载,非 ES Module。
 * 目录:
 *   1  SVG 图标库     2  API 封装   3  工具(fmtSize/fmtTime/esc + MCUtils 命名空间)
 *   4  面板标题图标   5  页面切换   6  连接管理           7  连接状态
 *   8  概览监控       9  数据库    10  用户与连接        11  数据库服务状态/重启
 *  12  用户管理      13  备份与还原 14  进度弹窗          15  定时备份(多任务)
 *  16  日志          17  服务设置   18  首次部署三步引导   19  数据看板
 *  20  服务器变量    21  告警中心   22  系统设置          23  主题切换(浅色/暗色)
 *  24  初始化        25  软件更新
 * 规范:新增顶层逻辑必须容错非对象响应(fetch stub 返回 [],见 DEVLOG R1/R9 教训);
 *      删除 DOM 元素后必须全局 grep 其 ID;供 inline onclick 的全局桥一律 window.xxx 显式挂载。
 */
"use strict";

/* 跨环境动画帧调度:jsdom/旧环境无 requestAnimationFrame 时回退 setTimeout */
function nextFrame(fn) {
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(fn);
  else setTimeout(fn, 16);
}

/* ---------- SVG 图标库（Lucide 风格，替换 Unicode/emoji） ---------- */
/* 统一带 width/height，避免无尺寸约束时 SVG 默认 300×150 撑爆布局 */
const ICON = {
  ok: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>',
  err: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>',
  warn: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4"/><path d="M12 17h.01"/></svg>',
  info: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 8h.01"/><path d="M12 12v4"/></svg>',
  folder: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>',
  file: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg>',
  bell: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/></svg>',
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const PAGES = {
  overview: { title: "概览监控", sub: "MySQL 服务器实时状态" },
  dashboard: { title: "数据看板", sub: "健康评分与引擎指标" },
  variables: { title: "服务器变量", sub: "SHOW VARIABLES 全量浏览" },
  databases: { title: "数据库", sub: "库与表结构" },
  users: { title: "用户与连接", sub: "用户、权限与实时连接" },
  query: { title: "SQL 查询", sub: "只读查询 · 结果最多 500 行" },
  backup: { title: "备份与还原", sub: "异步备份 / 还原与历史" },
  schedule: { title: "定时备份", sub: "定时自动备份任务" },
  settings: { title: "系统设置", sub: "账户、模式与软件更新" },
  alerts: { title: "告警中心", sub: "阈值检查与告警" },
  connections: { title: "连接管理", sub: "多连接配置 · 密码加密存储" },
  logs: { title: "操作日志", sub: "操作全程留痕" },
};

/* ---------- API ---------- */
/**
 * 统一 API 封装:JSON 请求 + 401 自动跳登录页 + 非 2xx 抛可读错误。
 * @param {string} method HTTP 方法(GET/POST/PUT/DELETE)
 * @param {string} path   接口路径,如 "/api/connections"
 * @param {object} [body] JSON 请求体(可选)
 * @returns {Promise<object>} 响应 JSON
 */
async function api(method, path, body) {
  const opt = { method, headers: {} };
  if (body !== undefined) {
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  // 携带认证 token
  const token = localStorage.getItem("mc_token");
  if (token) opt.headers["Authorization"] = "Bearer " + token;
  // 携带访问令牌(0.0.0.0 暴露时必需)
  const at = localStorage.getItem("mc_access_token");
  if (at) opt.headers["X-Access-Token"] = at;
  const res = await fetch(path, opt);
  let data = null;
  try { data = await res.json(); } catch (e) {}
  if (res.status === 401) {
    if (data && data.access_required) {
      promptAccessToken();
      throw new Error(data.error || "需要访问令牌");
    }
    localStorage.removeItem("mc_token");
    if (location.pathname !== "/login.html") location.href = "/login.html";
  }
  if (!res.ok) throw new Error(data && data.error ? data.error : `HTTP ${res.status}`);
  return data;
}

// 服务器开启访问令牌保护时,向用户收集令牌并保存后刷新
function promptAccessToken() {
  const at = window.prompt("该 MySQL Console 已开启访问令牌保护,请输入访问令牌:", "");
  if (at) {
    localStorage.setItem("mc_access_token", at.trim());
    location.reload();
  }
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
/* ---------- 工具命名空间(MCUtils):纯函数集中收纳,供测试/后续拆分引用 ---------- */
/* 原函数声明保持不变,调用点零改动;window.MCUtils 供 jsdom 测试与未来模块化直接取用。 */
const MCUtils = { fmtSize, fmtTime, esc };
window.MCUtils = MCUtils;
function setStatus(el, text, cls) {
  el.textContent = text || "";
  el.className = "inline-status" + (cls ? " " + cls : "");
}
/**
 * 统一确认对话框(替代裸 confirm):Promise 语义,body 支持 HTML。
 * @param {string} title 标题
 * @param {string} body  正文(可含 <br>/<b> 等)
 * @returns {Promise<boolean>} 点「确认」→ true,「取消」→ false
 */
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
  el.className = "toast " + (ok ? "ok" : "err");
  el.innerHTML = (ok ? ICON.ok : ICON.err) + "<span></span>";
  el.querySelector("span").textContent = text;
  document.body.appendChild(el);
  setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 260); }, 3000);
}

/* ---------- 面板标题图标装饰（关键词匹配注入） ---------- */
const PH_ICONS = [
  { kw: ["概览", "实时监控", "当前连接"], svg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12h4l3-8 4 16 3-8h4"/></svg>' },
  { kw: ["健康评分"], svg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 21s-7-4.6-9.3-9A5.4 5.4 0 0 1 12 6.3 5.4 5.4 0 0 1 21.3 12C19 16.4 12 21 12 21z"/></svg>' },
  { kw: ["InnoDB"], svg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/></svg>' },
  { kw: ["表空间"], svg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="m7 14 4-4 3 3 5-6"/></svg>' },
  { kw: ["复制状态"], svg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="4" width="20" height="14" rx="2"/><path d="M8 21h8"/><path d="M12 18v3"/></svg>' },
  { kw: ["数据库列表", "数据库一览"], svg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5"/><path d="M3 12a9 3 0 0 0 18 0"/></svg>' },
  { kw: ["表结构"], svg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18"/><path d="M3 15h18"/><path d="M9 3v18"/></svg>' },
  { kw: ["用户管理", "MySQL 用户"], svg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 5.5a3.5 3.5 0 0 1 0 6.7"/><path d="M17.5 14.5a6.5 6.5 0 0 1 4 5.5"/></svg>' },
  { kw: ["执行备份"], svg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M4 19h16"/></svg>' },
  { kw: ["执行还原"], svg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/></svg>' },
  { kw: ["备份/还原历史"], svg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l3 2"/></svg>' },
  { kw: ["备份文件"], svg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>' },
  { kw: ["自动备份任务"], svg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/></svg>' },
  { kw: ["数据库连接"], svg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/></svg>' },
  { kw: ["告警中心"], svg: ICON.bell },
  { kw: ["告警阈值"], svg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3"/><path d="M1 14h6M9 8h6M17 16h6"/></svg>' },
  { kw: ["服务器变量"], svg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2 3 14h7l-1 8 10-12h-7l1-8z"/></svg>' },
  { kw: ["账户设置"], svg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></svg>' },
  { kw: ["系统信息"], svg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 8h.01"/><path d="M12 12v4"/></svg>' },
  { kw: ["软件更新"], svg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/></svg>' },
  { kw: ["操作日志"], svg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6h16"/><path d="M4 12h10"/><path d="M4 18h7"/></svg>' },
];
function decoratePanelHeaders() {
  $$(".panel-head h3").forEach((h3) => {
    if (h3.querySelector("svg")) return;
    const t = h3.textContent || "";
    const hit = PH_ICONS.find((x) => x.kw.some((k) => t.includes(k)));
    const ico = document.createElement("span");
    ico.className = "ph-ico";
    ico.innerHTML = hit ? hit.svg : ICON.info;
    h3.prepend(ico);
  });
}

/* ---------- 页面切换 ---------- */
function switchPage(name) {
  $$(".nav-item").forEach((el) => el.classList.toggle("active", el.dataset.page === name));
  $$(".page").forEach((el) => el.classList.toggle("hidden", el.id !== "page-" + name));
  $("#page-title").textContent = PAGES[name].title;
  const subEl = $("#page-sub");
  if (subEl && PAGES[name].sub) subEl.textContent = PAGES[name].sub;
  decoratePanelHeaders();
  if (name === "overview") loadOverview();
  if (name === "databases") { loadDatabases(); loadDbServiceStatus(); }
  if (name === "users") { loadUserMgmt(); loadUsers(); loadProcesslist(); }
  if (name === "query") loadQueryPage();
  if (name === "backup") { loadBackupPage(); }
  if (name === "schedule") loadSchedule();
  if (name === "connections") loadConnections();
  if (name === "settings") loadUpdatePanel();
  if (name === "logs") loadLogs();
  if (name === "settings") loadSettingsPage();
  if (name === "dashboard") loadDashboardPage();
  if (name === "alerts") loadAlertsPage();
  if (name === "variables") loadVariablesPage();
  /* 页面由 display:none 变为可见后,容器尺寸才真正生效。布局完成前 echarts.init 会量到 0 尺寸并写死缓存,
     导致图表只有坐标轴骨架却不绘制内容(数据看板环形图/柱状图、告警历史图等)。等双 rAF 布局稳定后统一重算。 */
  nextFrame(() => nextFrame(() => {
    $$(`#page-${name} .chart`).forEach((el) => {
      const c = echarts.getInstanceByDom(el);
      if (c && c.resize) c.resize();
    });
  }));
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
  // 更新连接胶囊的本机/远程标签
  const tag = $("#conn-scope-tag");
  if (tag) {
    const active = connList.find((c) => c.active) || connList.find((c) => c.id === cur);
    if (!active) { tag.textContent = "未连接"; tag.className = "pill-tag"; return; }
    const h = String(active.host || "").toLowerCase();
    const local = h === "127.0.0.1" || h === "localhost" || h === "::1";
    tag.textContent = local ? "本机" : "远程";
    tag.className = "pill-tag" + (local ? "" : " remote");
  }
  updateMonitorTabs();
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

$("#cf-ssh-enabled").onclick = () => {
  $("#cf-ssh-fields").classList.toggle("hidden", !$("#cf-ssh-enabled").checked);
};

function isLocalHost(host) {
  const h = (host || "").trim().toLowerCase();
  return ["localhost", "127.0.0.1", "::1", "0.0.0.0", "0:0:0:0:0:0:0:1"].includes(h);
}

function updateBackupPathFields(host) {
  const local = isLocalHost(host);
  const l = document.getElementById("cf-backup-local");
  const r = document.getElementById("cf-backup-remote");
  if (l) l.classList.toggle("hidden", !local);
  if (r) r.classList.toggle("hidden", local);
}

// 远程服务器类型 → 配置指引面板(Linux 免配置 / Windows 需 Git Bash)
const REMOTE_OS_GUIDE = {
  linux: '<b>Linux 服务器</b>：无需额外配置，确保 sshd 可登录、远程目录可写即可。<br>目录示例：<code>~/mysql-console-backups</code>',
  windows: '<b>Windows 服务器</b>：远端命令为 Unix 语法，需把 OpenSSH 默认 shell 改为 Git Bash：<br>' +
    '<code>New-ItemProperty -Path "HKLM:\\SOFTWARE\\OpenSSH" -Name DefaultShell -Value "C:\\Program Files\\Git\\bin\\bash.exe" -PropertyType String -Force; Restart-Service sshd</code><br>' +
    '远程目录用 Git Bash 风格，如 <code>/c/mysql-console-backups</code>。<br>验证：本机执行 <code>ssh 用户@主机 "uname -s"</code> 应输出 MINGW/MSYS 字样。'
};
function updateRemoteGuide(os) {
  const g = document.getElementById("cf-remote-guide");
  if (!g) return;
  if (REMOTE_OS_GUIDE[os]) {
    g.innerHTML = REMOTE_OS_GUIDE[os];
    g.classList.remove("hidden");
  } else {
    g.classList.add("hidden");
  }
}

$("#cf-host").addEventListener("input", (e) => updateBackupPathFields(e.target.value));
$("#cf-btn-pick-backup-dir").onclick = () => pickDirInto("#cf-backup-dir", "选择本地备份目录");
$("#cf-remote-os").addEventListener("change", (e) => updateRemoteGuide(e.target.value));

// 测试远程环境:SSH 探测服务器 OS 并自动回填
$("#cf-btn-remote-check").onclick = async () => {
  const body = connFormBody();
  if (!body.ssh_host) { toast("请先填写 SSH 主机(上方 SSH 隧道配置)", false); return; }
  const btn = $("#cf-btn-remote-check");
  btn.disabled = true; btn.textContent = "探测中...";
  try {
    const r = await post("/api/connections/remote-check", body);
    if (r.ok) {
      $("#cf-remote-os").value = r.os || "";
      updateRemoteGuide(r.os || "");
      if (r.os === "linux") {
        toast("检测为 Linux 服务器");
      } else if (r.os === "windows") {
        if (r.git_bash) { toast("检测为 Windows（Git Bash 环境就绪）"); }
        else { toast("检测为 Windows（未检测到 Git Bash，需按指引配置）", false); updateRemoteGuide("windows"); }
      } else {
        toast("未能识别服务器类型", false);
      }
    } else {
      toast(r.error, false);
    }
  } catch (e) {
    toast("探测异常: " + e.message, false);
  } finally {
    btn.disabled = false; btn.textContent = "测试远程环境";
  }
};

function editConn(id) {
  editingConnId = id;
  const c = connList.find((x) => x.id === id);
  $("#conn-form-title").textContent = "编辑连接";
  $("#cf-name").value = c.name; $("#cf-host").value = c.host;
  $("#cf-port").value = c.port; $("#cf-user").value = c.user;
  $("#cf-pass").value = ""; $("#cf-note").value = c.note || "";
  // SSH 隧道字段加载
  const en = c.ssh_enabled === true || c.ssh_enabled === 1 || c.ssh_enabled === "1";
  $("#cf-ssh-enabled").checked = en;
  ["ssh_host", "ssh_port", "ssh_user", "ssh_key", "ssh_bind_host", "ssh_bind_port"].forEach((f) => {
    const val = c[f];
    const el = $("#cf-" + f);
    if (el) el.value = val == null ? (f === "ssh_port" ? 22 : "") : val;
  });
  $("#cf-ssh-fields").classList.toggle("hidden", !en);
  // 备份目录字段加载
  $("#cf-backup-dir").value = c.backup_dir || "";
  $("#cf-remote-backup-dir").value = c.remote_backup_dir || "";
  $("#cf-remote-os").value = c.remote_os || "";
  $("#cf-db-version").value = c.db_version || "";
  updateRemoteGuide(c.remote_os || "");
  updateBackupPathFields(c.host);
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
  $("#cf-ssh-enabled").checked = false;
  $("#cf-ssh-host").value = "";
  $("#cf-ssh-port").value = 22;
  $("#cf-ssh-user").value = "";
  $("#cf-ssh-key").value = "";
  $("#cf-ssh-bind-host").value = "";
  $("#cf-ssh-bind-port").value = 0;
  $("#cf-ssh-fields").classList.add("hidden");
  $("#cf-backup-dir").value = "";
  $("#cf-remote-backup-dir").value = "";
  $("#cf-remote-os").value = "";
  $("#cf-db-version").value = "";
  updateRemoteGuide("");
  updateBackupPathFields($("#cf-host").value);
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
    ssh_enabled: $("#cf-ssh-enabled").checked,
    ssh_host: $("#cf-ssh-host").value.trim(),
    ssh_port: parseInt($("#cf-ssh-port").value || 22),
    ssh_user: $("#cf-ssh-user").value.trim(),
    ssh_key: $("#cf-ssh-key").value.trim(),
    ssh_bind_host: $("#cf-ssh-bind-host").value.trim(),
    ssh_bind_port: parseInt($("#cf-ssh-bind-port").value || 0),
    backup_dir: $("#cf-backup-dir").value.trim(),
    remote_backup_dir: $("#cf-remote-backup-dir").value.trim(),
    remote_os: $("#cf-remote-os").value.trim(),
    db_version: $("#cf-db-version").value.trim(),
  };
}
window.connFormBody = connFormBody;   // window.* 桥:供 jsdom 回归与后续拆分取用

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
  updateMonitorTabs();
}

$("#conn-select").onchange = async (e) => {
  const id = e.target.value;
  if (!id) { updateConnStatus(false); return; }
  try { await post("/api/connect", { id }); updateConnStatus(true); toast("已切换到 " + id); }
  catch (err) { updateConnStatus(false); toast("激活失败: " + err.message, false); }
};

/* ---------- 概览监控 ---------- */
let _datadir = "";
const charts = {};
const S = { conn: [], qps: [], cpu: [], net: [], hit: [], ioR: [], ioW: [], repl: [] };
let _monitorPoints = 60; /* 实时监控窗口点数(5 分钟=60/15 分钟=180/1 小时=720) */

/* ECharts canvas 不支持 CSS 变量，运行时解析主题实际色值供图表使用 */
function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "";
}
function chartText() {
  return {
    text: cssVar("--text") || "#1a2233",
    text2: cssVar("--text-2") || "#5b6577",
    text3: cssVar("--text-3") || "#8a93a6",
    border: cssVar("--border-2") || "#d5dce7",
    panel: cssVar("--panel") || "#ffffff",
  };
}

/* Gauge 健康配色：invert=true 时值越大越健康（如命中率） */
function gaugeColor(v, warnAt, dangerAt, invert) {
  if (v == null || isNaN(v)) return "#8a93a6";
  if (invert) { if (v < dangerAt) return "#b33434"; if (v < warnAt) return "#b57d1a"; return "#3a6f10"; }
  if (v > dangerAt) return "#b33434"; if (v > warnAt) return "#b57d1a"; return "#3a6f10";
}

function lineOpt(title, color, yName) {
  const tc = chartText();
  return {
    grid: { left: 52, right: 18, top: 38, bottom: 30 },
    tooltip: { trigger: "axis", backgroundColor: tc.panel, borderColor: tc.border, textStyle: { color: tc.text } },
    title: { text: title, textStyle: { fontSize: 13, fontWeight: 500, color: tc.text } },
    xAxis: { type: "category", data: Array.from({ length: 60 }, (_, i) => i), axisLabel: { color: tc.text3, fontSize: 10, formatter: (v) => v % 12 === 0 ? `-${v * 5}s` : "" }, axisLine: { lineStyle: { color: tc.border } } },
    yAxis: { type: "value", minInterval: 1, axisLabel: { color: tc.text3, fontSize: 10 }, nameTextStyle: { color: tc.text2 }, splitLine: { lineStyle: { color: tc.border, opacity: .4 } } },
    series: [{ type: "line", smooth: true, showSymbol: false, data: [], lineStyle: { width: 2, color }, itemStyle: { color }, areaStyle: { opacity: 0.08, color } }],
  };
}

function gaugeOpt(name, max, unit) {
  const tc = chartText();
  return {
    series: [{
      type: "gauge", min: 0, max: max || 100, radius: "95%",
      progress: { show: true, width: 10 },
      axisLine: { lineStyle: { width: 10, color: [[1, tc.border]] } },
      axisTick: { show: false }, splitLine: { show: false },
      axisLabel: { show: false }, pointer: { show: false },
      title: { show: true, offsetCenter: [0, "42%"], fontSize: 13, color: tc.text2 },
      detail: { valueAnimation: true, fontSize: 22, offsetCenter: [0, "2%"], formatter: (v) => v + (unit || "%"), color: tc.text },
      data: [{ value: 0, name }],
    }],
  };
}

/* 健康评分趋势:时间轴折线 + 警戒/较差参考线 */
function trendOpt(title, color) {
  const tc = chartText();
  return {
    grid: { left: 44, right: 16, top: 38, bottom: 26 },
    tooltip: {
      trigger: "axis", backgroundColor: tc.panel, borderColor: tc.border, textStyle: { color: tc.text },
      formatter: (ps) => {
        const p = ps && ps[0];
        if (!p || p.value == null) return "";
        const d = new Date(p.value[0]);
        const hh = String(d.getHours()).padStart(2, "0"), mm = String(d.getMinutes()).padStart(2, "0");
        return `${d.getMonth() + 1}-${d.getDate()} ${hh}:${mm}<br/>评分 <b>${p.value[1]}</b>`;
      },
    },
    title: { text: title, textStyle: { fontSize: 13, fontWeight: 500, color: tc.text } },
    xAxis: {
      type: "time",
      axisLabel: { color: tc.text3, fontSize: 10, hideOverlap: true, formatter: (v) => { const d = new Date(v); return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`; } },
      axisLine: { lineStyle: { color: tc.border } },
    },
    yAxis: { type: "value", min: 0, max: 100, axisLabel: { color: tc.text3, fontSize: 10 }, nameTextStyle: { color: tc.text2 }, splitLine: { lineStyle: { color: tc.border, opacity: .4 } } },
    series: [{
      type: "line", smooth: true, showSymbol: false, data: [],
      lineStyle: { width: 2, color }, itemStyle: { color },
      areaStyle: { opacity: 0.10, color },
      markLine: {
        silent: true, symbol: "none",
        data: [
          { yAxis: 75, lineStyle: { type: "dashed", color: "#b57d1a" }, label: { formatter: "警戒 75", color: "#b57d1a", fontSize: 10, position: "insideEndTop" } },
          { yAxis: 60, lineStyle: { type: "dashed", color: "#b33434" }, label: { formatter: "较差 60", color: "#b33434", fontSize: 10, position: "insideEndTop" } },
        ],
      },
    }],
  };
}

/* 环形图(数据库空间占比) */
function donutOpt(title) {
  const tc = chartText();
  return {
    title: { text: title, textStyle: { fontSize: 13, fontWeight: 500, color: tc.text } },
    tooltip: {
      trigger: "item", backgroundColor: tc.panel, borderColor: tc.border, textStyle: { color: tc.text },
      formatter: (p) => `${p.name}<br/>${fmtSize(p.value)} · ${p.percent}%`,
    },
    legend: { bottom: 0, left: "center", type: "scroll", textStyle: { fontSize: 11, color: tc.text2 }, itemWidth: 10, itemHeight: 10 },
    series: [{
      type: "pie", radius: ["44%", "66%"], center: ["50%", "42%"],
      avoidLabelOverlap: true,
      itemStyle: { borderRadius: 4, borderColor: tc.panel, borderWidth: 2 },
      label: { show: true, formatter: "{b}\n{d}%", fontSize: 11, color: tc.text2, lineHeight: 15 },
      labelLine: { length: 8, length2: 8 },
      emphasis: { label: { fontSize: 12, fontWeight: 600 } },
      data: [],
    }],
  };
}

/* 横向条形图(表空间 Top N) */
function hbarOpt(title) {
  const tc = chartText();
  return {
    grid: { left: 8, right: 52, top: 38, bottom: 8, containLabel: true },
    tooltip: {
      trigger: "axis", axisPointer: { type: "shadow" }, backgroundColor: tc.panel, borderColor: tc.border, textStyle: { color: tc.text },
      formatter: (ps) => {
        const p = ps && ps[0];
        if (!p) return "";
        const rows = p.data && p.data.rows != null ? ` · ${fmtNum(p.data.rows)} 行` : "";
        return `${p.name}<br/>${fmtSize(p.value)}${rows}`;
      },
    },
    xAxis: {
      type: "value", axisLabel: { color: tc.text3, fontSize: 10, formatter: (v) => fmtSize(v) },
      splitLine: { lineStyle: { color: tc.border, opacity: .4 } },
    },
    yAxis: {
      type: "category", inverse: true,
      axisLabel: { color: tc.text2, fontSize: 11, width: 108, overflow: "truncate" },
      axisLine: { lineStyle: { color: tc.border } }, axisTick: { show: false },
    },
    series: [{
      type: "bar", data: [], barWidth: 12,
      itemStyle: { color: "#185fa5", borderRadius: [0, 4, 4, 0] },
      label: { show: true, position: "right", color: tc.text3, fontSize: 10, formatter: (p) => fmtSize(p.value) },
    }],
  };
}

/* 数据看板图表懒加载:看板页初始为 hidden,display:none 时 echarts.init 尺寸为 0,故首次进入看板页再创建 */
function ensureDashboardCharts() {
  if (charts.healthTrend) return;
  const c = charts;
  c.healthTrend = echarts.init($("#chart-health-trend"));
  c.healthTrend.setOption(trendOpt("健康评分趋势", "#3a6f10"));
  c.dbDonut = echarts.init($("#chart-db-donut"));
  c.dbDonut.setOption(donutOpt("数据库空间占比"));
  c.tsBar = echarts.init($("#chart-ts-bar"));
  c.tsBar.setOption(hbarOpt("表空间大小 Top 10"));
  addChartExport(".chart-box");
}

/* 告警历史趋势:堆积条形图(警告/严重) */
function alertHistOpt() {
  const tc = chartText();
  return {
    grid: { left: 40, right: 16, top: 30, bottom: 24 },
    tooltip: {
      trigger: "axis", axisPointer: { type: "shadow" }, backgroundColor: tc.panel, borderColor: tc.border, textStyle: { color: tc.text },
      formatter: (ps) => {
        const p = ps && ps[0];
        if (!p || p.axisValue == null) return "";
        const d = new Date(p.axisValue);
        const hh = String(d.getHours()).padStart(2, "0"), mm = String(d.getMinutes()).padStart(2, "0");
        const lines = ps.map((s) => `${s.marker}${s.seriesName}: <b>${s.value}</b>`).join("<br/>");
        return `${d.getMonth() + 1}-${d.getDate()} ${hh}:${mm}<br/>${lines}`;
      },
    },
    legend: { top: 0, right: 4, textStyle: { fontSize: 11, color: tc.text2 }, itemWidth: 10, itemHeight: 10 },
    xAxis: {
      type: "time",
      axisLabel: { color: tc.text3, fontSize: 10, hideOverlap: true, formatter: (v) => { const d = new Date(v); return `${d.getMonth() + 1}-${d.getDate()}`; } },
      axisLine: { lineStyle: { color: tc.border } },
    },
    yAxis: { type: "value", minInterval: 1, axisLabel: { color: tc.text3, fontSize: 10 }, nameTextStyle: { color: tc.text2 }, splitLine: { lineStyle: { color: tc.border, opacity: .4 } } },
    series: [
      { name: "警告", type: "bar", stack: "a", barMaxWidth: 18, data: [], itemStyle: { color: "#b57d1a" } },
      { name: "严重", type: "bar", stack: "a", barMaxWidth: 18, data: [], itemStyle: { color: "#b33434", borderRadius: [0, 2, 2, 0] } },
    ],
  };
}

/* 告警页图表懒加载(告警页初始 hidden) */
function ensureAlertsCharts() {
  if (charts.alertHist) return;
  charts.alertHist = echarts.init($("#chart-alert-history"));
  charts.alertHist.setOption(alertHistOpt());
  addChartExport(".chart-box");
}

function initCharts() {
  const c = charts;
  c.conn = echarts.init($("#chart-conn")); c.conn.setOption(lineOpt("连接数", "#185fa5"));
  c.qps = echarts.init($("#chart-qps")); c.qps.setOption(lineOpt("每秒查询数 QPS", "#1d9e75"));
  /* 系统资源 */
  c.gCpu = echarts.init($("#gauge-cpu")); c.gCpu.setOption(gaugeOpt("CPU 使用率", 100, "%"));
  c.gMem = echarts.init($("#gauge-mem")); c.gMem.setOption(gaugeOpt("内存使用率", 100, "%"));
  c.gDisk = echarts.init($("#gauge-disk")); c.gDisk.setOption(gaugeOpt("磁盘空间", 100, "%"));
  c.gIo = echarts.init($("#gauge-io")); c.gIo.setOption(gaugeOpt("磁盘 IOPS", 100, ""));
  c.cCpu = echarts.init($("#chart-cpu")); c.cCpu.setOption(lineOpt("CPU 使用率趋势 (%)", "#b57d1a"));
  c.cNet = echarts.init($("#chart-net")); c.cNet.setOption(lineOpt("网络吞吐 (KB/s)", "#5b6577"));
  /* InnoDB */
  c.gHit = echarts.init($("#gauge-hit")); c.gHit.setOption(gaugeOpt("缓冲池命中率", 100, "%"));
  c.gDirty = echarts.init($("#gauge-dirty")); c.gDirty.setOption(gaugeOpt("脏页比例", 100, "%"));
  c.gLock = echarts.init($("#gauge-lock")); c.gLock.setOption(gaugeOpt("行锁等待", 50, "/s"));
  c.cHit = echarts.init($("#chart-hit")); c.cHit.setOption(lineOpt("命中率趋势 (%)", "#2f76bd"));
  c.cIo = echarts.init($("#chart-io"));
  {
    const tc = chartText();
    c.cIo.setOption({
      grid: { left: 52, right: 18, top: 38, bottom: 30 }, tooltip: { trigger: "axis", backgroundColor: tc.panel, borderColor: tc.border, textStyle: { color: tc.text } },
      title: { text: "InnoDB 读写吞吐 (KB/s)", textStyle: { fontSize: 13, fontWeight: 500, color: tc.text } },
      xAxis: { type: "category", data: Array.from({ length: 60 }, (_, i) => i), axisLabel: { color: tc.text3, fontSize: 10, formatter: (v) => v % 12 === 0 ? `-${v * 5}s` : "" }, axisLine: { lineStyle: { color: tc.border } } },
      yAxis: { type: "value", axisLabel: { color: tc.text3, fontSize: 10 }, nameTextStyle: { color: tc.text2 }, splitLine: { lineStyle: { color: tc.border, opacity: .4 } } },
      legend: { top: 2, right: 6, textStyle: { fontSize: 11, color: tc.text2 } },
      series: [
        { name: "读", type: "line", smooth: true, showSymbol: false, data: [], lineStyle: { width: 2, color: "#185fa5" }, itemStyle: { color: "#185fa5" } },
        { name: "写", type: "line", smooth: true, showSymbol: false, data: [], lineStyle: { width: 2, color: "#1d9e75" }, itemStyle: { color: "#1d9e75" } },
      ],
    });
  }
  /* 复制 */
  c.gRepl = echarts.init($("#gauge-repl")); c.gRepl.setOption(gaugeOpt("复制延迟", 60, "s"));
  c.cRepl = echarts.init($("#chart-repl")); c.cRepl.setOption(lineOpt("复制延迟趋势 (s)", "#854f0b"));
}

function setGauge(ch, value, color, max) {
  if (!ch || value == null) return;
  const s = ch.getOption().series[0];
  const opt = { series: [{ data: [{ value: Math.round(value * 10) / 10, name: s.data[0].name, itemStyle: { color } }] }] };
  if (max) opt.series[0].max = max;
  ch.setOption(opt);
}

function pushSeries(key, v) {
  const arr = S[key];
  arr.push(v); if (arr.length > _monitorPoints) arr.shift();
}

/* 实时监控时间范围切换:调整窗口点数并重设折线图横轴 */
function setMonitorRange(n) {
  _monitorPoints = n;
  Object.keys(S).forEach((k) => { if (S[k].length > n) S[k] = S[k].slice(-n); });
  const data = Array.from({ length: n }, (_, i) => i);
  ["conn", "qps", "cCpu", "cNet", "cHit", "cIo", "cRepl"].forEach((k) => {
    const c = charts[k];
    if (c) c.setOption({ xAxis: { data } });
  });
}
$("#monitor-range-seg").addEventListener("click", (e) => {
  const b = e.target.closest(".seg-btn");
  if (!b) return;
  $$("#monitor-range-seg .seg-btn").forEach((x) => x.classList.toggle("active", x === b));
  setMonitorRange(parseInt(b.dataset.n, 10));
});

/* 阈值参考线:连接数阈值(来自设置)与 CPU 警戒线 */
async function applyThresholdLines() {
  try {
    const s = await get("/api/settings");
    const maxConn = parseInt((s.settings && s.settings.alert_max_conn) || 100, 10);
    const conn = charts.conn;
    if (conn) conn.setOption({
      series: [{ markLine: { silent: true, symbol: "none", data: [
        { yAxis: maxConn, lineStyle: { color: "#b57d1a", type: "dashed" }, label: { formatter: "连接阈值 " + maxConn, color: "#b57d1a", fontSize: 10, position: "insideEndTop" } },
      ] } }],
    });
  } catch (e) {}
  const cpu = charts.cCpu;
  if (cpu) cpu.setOption({
    series: [{ markLine: { silent: true, symbol: "none", data: [
      { yAxis: 80, lineStyle: { color: "#b33434", type: "dashed" }, label: { formatter: "警戒 80%", color: "#b33434", fontSize: 10, position: "insideEndTop" } },
    ] } }],
  });
}

/* 每个图表框右上角注入导出 PNG 按钮(悬停显示,触屏常显) */
function addChartExport(boxSel) {
  document.querySelectorAll(boxSel).forEach((box) => {
    if (!box.classList.contains("chart-box") || box.querySelector(".chart-export")) return;
    const el = box.querySelector(".chart");
    if (!el || !el.id) return;
    const inst = echarts.getInstanceByDom(el);
    if (!inst) return;
    const btn = document.createElement("button");
    btn.className = "chart-export";
    btn.title = "导出 PNG";
    btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/></svg>';
    btn.onclick = (ev) => {
      ev.stopPropagation();
      const url = inst.getDataURL({ type: "png", pixelRatio: 2, backgroundColor: cssVar("--panel") || "#ffffff" });
      const a = document.createElement("a");
      a.href = url;
      a.download = el.id + "-" + Date.now() + ".png";
      a.click();
    };
    box.appendChild(btn);
  });
}

/* 主题切换时刷新全部图表文字/坐标轴颜色（canvas 不支持 CSS 变量） */
function refreshChartColors() {
  const tc = chartText();
  const base = {
    title: { textStyle: { color: tc.text } },
    legend: { textStyle: { color: tc.text2 } },
    xAxis: { axisLabel: { color: tc.text3 }, axisLine: { lineStyle: { color: tc.border } } },
    yAxis: { axisLabel: { color: tc.text3 }, nameTextStyle: { color: tc.text2 }, splitLine: { lineStyle: { color: tc.border, opacity: .4 } } },
    tooltip: { backgroundColor: tc.panel, borderColor: tc.border, textStyle: { color: tc.text } },
  };
  ["conn", "qps", "cCpu", "cNet", "cHit", "cIo", "cRepl", "healthTrend", "tsBar", "alertHist"].forEach((k) => {
    const c = charts[k];
    if (c) c.setOption(base);
  });
  /* 环形图(库占比):无 x/y 轴,单独刷新文字/图例/提示/扇区分隔 */
  const d = charts.dbDonut;
  if (d) d.setOption({
    title: { textStyle: { color: tc.text } },
    legend: { textStyle: { color: tc.text2 } },
    tooltip: { backgroundColor: tc.panel, borderColor: tc.border, textStyle: { color: tc.text } },
    series: [{ label: { color: tc.text2 }, itemStyle: { borderColor: tc.panel } }],
  });
  ["gCpu", "gMem", "gDisk", "gIo", "gHit", "gDirty", "gLock", "gRepl"].forEach((k) => {
    const g = charts[k];
    if (!g) return;
    /* Gauge 的 title/detail/axisLine 在 series[0] 内，必须以 series: [{...}] 形式 setOption */
    g.setOption({
      series: [{
        title: { color: tc.text2 },
        detail: { color: tc.text },
        axisLine: { lineStyle: { width: 10, color: [[1, tc.border]] } },
      }],
    });
  });
}

/* 实时监控 Tab 切换 */
$$(".mtab").forEach((btn) => {
  btn.onclick = () => {
    $$(".mtab").forEach((b) => b.classList.toggle("active", b === btn));
    $$(".mtab-pane").forEach((p) => p.classList.toggle("active", p.id === "mtabp-" + btn.dataset.mtab));
    const pane = $("#mtabp-" + btn.dataset.mtab);
    if (pane) pane.querySelectorAll(".chart").forEach((el) => { const c = echarts.getInstanceByDom(el); if (c) c.resize(); });
  };
});
/* 系统资源 Tab：仅本机连接显示（被管远程 DB 无 OS 数据） */
function updateMonitorTabs() {
  const sysBtn = document.querySelector('.mtab[data-mtab="sysres"]');
  if (!sysBtn) return;
  const active = connList.find((c) => c.active);
  let local = false;
  if (active) {
    const h = String(active.host || "").toLowerCase();
    local = h === "127.0.0.1" || h === "localhost" || h === "::1";
  }
  sysBtn.style.display = local ? "" : "none";
  if (!local && $("#mtabp-sysres") && $("#mtabp-sysres").classList.contains("active")) {
    const m = document.querySelector('.mtab[data-mtab="metrics"]');
    if (m) m.click();
  }
}

async function loadOverview() {
  applyThresholdLines();
  try {
    const ov = await get("/api/overview");
    if (ov.datadir) _datadir = ov.datadir;
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
  const t = fmtTime(Math.floor(Date.now() / 1000));
  /* 指标 + InnoDB + 复制（合并接口） */
  try {
    const m = await get("/api/monitor/full");
    const tt = fmtTime(m.ts);
    pushSeries("conn", m.connections);
    charts.conn.setOption({ series: [{ data: S.conn }] });
    pushSeries("qps", m.qps);
    charts.qps.setOption({ series: [{ data: S.qps }] });
    const ino = m.innodb || {};
    setGauge(charts.gHit, ino.hit_rate, gaugeColor(ino.hit_rate, 95, 90, true));
    setGauge(charts.gDirty, ino.dirty_ratio, gaugeColor(ino.dirty_ratio, 10, 20, false));
    setGauge(charts.gLock, ino.lock_waits, ino.lock_waits > 5 ? "#b33434" : (ino.lock_waits > 1 ? "#b57d1a" : "#3a6f10"), Math.max(50, (ino.lock_waits || 0) * 2));
    pushSeries("hit", ino.hit_rate);
    charts.cHit.setOption({ series: [{ data: S.hit }] });
    pushSeries("ioR", ino.read_kbs || 0);
    pushSeries("ioW", ino.write_kbs || 0);
    charts.cIo.setOption({ series: [{ data: S.ioR }, { data: S.ioW }] });
    const rp = m.repl || {};
    if (rp.is_slave) {
      $("#mtab-repl").style.display = "";
      const lag = rp.seconds_behind == null ? 0 : rp.seconds_behind;
      setGauge(charts.gRepl, lag, gaugeColor(lag, 5, 30, false), Math.max(60, lag * 1.2));
      pushSeries("repl", lag);
      charts.cRepl.setOption({ series: [{ data: S.repl }] });
      $("#repl-status-items").innerHTML = [
        ["IO 线程", rp.io_running],
        ["SQL 线程", rp.sql_running],
      ].map(([lb, v]) => {
        const ok = String(v).toLowerCase() === "yes";
        return `<div class="repl-item"><span class="r-label">${lb}</span><span class="r-value"><span class="r-dot ${ok ? "ok" : "err"}"></span>${ok ? "运行中" : (v ? esc(v) : "未知")}</span></div>`;
      }).join("");
    }
  } catch (e) {}
  /* 系统资源（本机，独立接口） */
  try {
    const r = await get("/api/sys-resource?disk=" + encodeURIComponent(_datadir || ""));
    if (r.cpu_percent != null) setGauge(charts.gCpu, r.cpu_percent, gaugeColor(r.cpu_percent, 60, 80, false));
    if (r.mem_percent != null) setGauge(charts.gMem, r.mem_percent, gaugeColor(r.mem_percent, 60, 80, false));
    if (r.disk_percent != null) setGauge(charts.gDisk, r.disk_percent, gaugeColor(r.disk_percent, 70, 85, false));
    if (r.disk_io) setGauge(charts.gIo, r.disk_io.iops, "#3a6f10", Math.max(100, r.disk_io.iops * 1.5));
    if (r.cpu_percent != null) {
      pushSeries("cpu", r.cpu_percent);
      charts.cCpu.setOption({ series: [{ data: S.cpu }] });
    }
    if (r.net_kbs != null) {
      pushSeries("net", r.net_kbs);
      charts.cNet.setOption({ series: [{ data: S.net }] });
    }
    const tip = $("#sysres-tip");
    if (tip) tip.style.display = r.disk_io == null && !r.has_psutil ? "" : "none";
    const ioBox = $("#gauge-io") ? $("#gauge-io").closest(".chart-box") : null;
    if (ioBox) ioBox.style.display = r.disk_io ? "" : "none";
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

/* ---------- SHOW GRANTS 解析为 UI 模型(2026-08-28:设置权限带出现有授权) ---------- */
/* 返回 {scopeAll, databases[], privileges[], extra[]}
   - "GRANT USAGE ON *.*" 视为无实际权限,忽略
   - "ALL / ALL PRIVILEGES" → 全局 + 权限网格全选
   - ON *.* → 全部数据库;ON `db`.* → 指定库;表级/列级授权归入 extra(UI 仅管库级)
   - 界面网格之外的系统权限(PROCESS/SUPER/RELOAD 等)→ extra(保存按勾选覆盖)
   - WITH GRANT OPTION → 勾选 GRANT OPTION */
function parseGrants(lines) {
  const model = { scopeAll: false, databases: [], privileges: [], extra: [] };
  const dbSet = new Set();
  const privSet = new Set();
  const gridSet = new Set(UM_PRIVS);
  let allFlag = false, grantOpt = false;
  for (const raw of (lines || [])) {
    const line = String(raw);
    const m = /^GRANT\s+(.+?)\s+ON\s+(.+?)\s+TO\s+/i.exec(line);
    if (!m) continue;
    const privsPart = m[1].trim();
    const dbObj = m[2].trim();
    const withGrant = /WITH\s+GRANT\s+OPTION/i.test(line);
    const parts = privsPart.split(",").map((s) => s.trim()).filter(Boolean);
    const rowAll = parts.some((p) => /^ALL(\s+PRIVILEGES)?$/i.test(p));
    const real = parts.filter((p) => {
      const up = p.toUpperCase();
      return up !== "USAGE" && up !== "ALL" && up !== "ALL PRIVILEGES";
    });
    // 纯 USAGE 占位行(每个用户默认输出,表示无实际权限)不参与归集,避免误判全局授权
    if (!real.length && !rowAll && !withGrant) continue;
    if (rowAll) allFlag = true;
    if (withGrant) grantOpt = true;
    if (dbObj === "*.*") {
      model.scopeAll = true;
    } else {
      const dm = /^`([^`]+)`\.\*$/.exec(dbObj);
      if (dm) dbSet.add(dm[1]);
      else model.extra.push("表级/列级授权 " + dbObj);
    }
    real.forEach((p) => {
      const up = p.toUpperCase();
      if (gridSet.has(up)) privSet.add(up);
      else model.extra.push(up);                                    // 界面外权限
    });
  }
  model.privileges = allFlag ? UM_PRIVS.slice() : Array.from(privSet);
  if (grantOpt && !model.privileges.includes("GRANT OPTION")) model.privileges.push("GRANT OPTION");
  model.databases = Array.from(dbSet);
  return model;
}
window.parseGrants = parseGrants;  // 全局桥(供 jsdom 回归/未来模块化直接取用,与 window.* 桥约定一致)
/* 编辑弹窗打开后拉取 SHOW GRANTS 并回填(范围/指定库选中/权限勾选)。
   拉取失败或解析为空 → 保持空勾选并提示;界面外授权提示「保存将按本次勾选覆盖」。 */
async function loadCurrentGrantsIntoModal(user, host) {
  try {
    const r = await get("/api/users/" + encodeURIComponent(user + "@" + host) + "/grants");
    const m = parseGrants(r.grants || []);
    if (m.scopeAll) {
      $('input[name="um-scope"][value="all"]').checked = true;
      $("#um-db-wrap").classList.add("hidden");
      if (m.databases.length) setStatus($("#um-status"), "现有授权含全局与指定库,按全局展示;保存将按当前勾选覆盖", "err");
    } else {
      $('input[name="um-scope"][value="pick"]').checked = true;
      $("#um-db-wrap").classList.remove("hidden");
      const opts = Array.from($("#um-dbs").options);
      m.databases.forEach((db) => {
        const o = opts.find((x) => x.value === db);
        if (o) o.selected = true;
      });
      if (m.databases.some((db) => !opts.some((o) => o.value === db))) {
        setStatus($("#um-status"), "部分授权库不在当前服务器库列表中,保存时请核对", "err");
      }
    }
    umSetPrivs(m.privileges);
    if (m.extra.length) {
      const extra = Array.from(new Set(m.extra)).join(", ");
      setStatus($("#um-status"), "该用户另有界面外的授权(" + extra + "),保存后将被本次勾选覆盖", "err");
    }
  } catch (e) {
    setStatus($("#um-status"), "加载现有授权失败:" + e.message, "err");
  }
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
  /* root 是超级管理员:授权不允许通过本工具修改(查看走 viewGrants 只读展示)。
     与后端 _handle_user_update 的 root 403 保护一致,双端拦截。 */
  if (String(user).toLowerCase() === "root") {
    await confirmDialog("不允许修改 root 授权",
      "root 是 MySQL 超级管理员,通过本工具修改其授权风险极高。<br>" +
      "如需调整,请在 MySQL 命令行直接执行 <code>GRANT</code> / <code>REVOKE</code>,<br>" +
      "日常业务建议使用专用账户并遵循最小权限原则。");
    return;
  }
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
  // 带出现有授权,基于现状修改(2026-08-28 需求);加载完成后再显示弹窗
  await loadCurrentGrantsIntoModal(user, host);
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
  if (p === "all") umSetPrivs(UM_PRIVS);  // 完整权限 = 全选所有细分权限
  else if (p) umSetPrivs(UM_PRESETS[p]);
  else umSetPrivs([]);
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
  // 当前激活连接为远程时,提示备份将写到 SSH 服务器(本地目录不可用)
  const hint = document.getElementById("bk-remote-hint");
  if (hint) {
    const active = (connList || []).find((c) => c.active);
    const remote = active && !isLocalHost(active.host);
    if (remote) {
      const osTag = active.remote_os === "windows" ? "[Windows 服务器]" :
                    active.remote_os === "linux" ? "[Linux 服务器]" : "";
      hint.textContent = "当前连接为远程数据库" + osTag + "：备份文件将经 SSH 直写服务器(" +
        (active.remote_backup_dir || "远程家目录 ~/mysql-console-backups") +
        ")，不落本地、不可直接下载。Windows 服务器需配 Git Bash（见连接管理→远程服务器类型指引）。";
      hint.classList.remove("hidden");
    } else {
      hint.classList.add("hidden");
    }
  }
  await loadRemoteRestoreUI();
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
        <td class="mono">${fmtSize(h.size)} ${h.path.toLowerCase().endsWith(".zip") ? '<span class="badge">ZIP</span>' : (h.compressed ? '<span class="badge">GZ</span>' : "")}</td><td class="mono">${h.elapsed}s</td>
        <td>${h.result === "success" ? '<span class="badge success">成功</span>' : `<span class="badge failed">失败</span>`}${h.warning ? ` <span class="badge running" title="${esc(h.warning)}">⚠</span>` : ""}</td>
        <td>${(h.type === "backup" && h.result === "success") ? (
          h.storage === "remote"
            ? `<button class="btn btn-sm" disabled title="文件在远程服务器，无法直接下载">下载</button> `
            : (h.exists ? `<button class="btn btn-sm" data-path="${esc(h.path)}" onclick="window.downloadBackup(this)">下载</button> ` : "")
        ) : ""}${h.result !== "success" && h.error ? `<button class="btn btn-sm" data-err="${esc(h.error)}" onclick="window.showErr(this)">错误</button>` : (h.warning ? `<button class="btn btn-sm" data-err="${esc(h.warning)}" onclick="window.showErr(this)">警告</button>` : "")}
        ${h.result === "success" && h.storage === "remote" ? `<button class="btn btn-sm" data-path="${esc(h.path)}" data-rid="${esc(h.id)}" onclick="window.restoreRemote(this)">远程还原</button>` : ""}</td>
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
      $("#pm-title").textContent = ok ? "操作完成" : "操作失败";
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

/* 高级备份/还原参数:输入框 = 本次执行完整参数(所见即所得) */
const _paramsCache = { backup_opts: "", restore_opts: "" };
function _splitOpts(s) {
  // ponytail: 简易 shlex——支持成对双/单引号包裹含空格的 token,不做转义
  const out = [];
  const re = /"([^"]*)"|'([^']*)'|(\S+)/g;
  let m;
  while ((m = re.exec(s || ""))) out.push(m[1] ?? m[2] ?? m[3]);
  return out;
}
let _bkBuiltinOpts = [], _rsBuiltinOpts = [];
async function initBackupParams() {
  try {
    const p = await get("/api/backup-params");  // 走 api() 封装,带认证头(裸 fetch 全量模式 401)
    if (!p || typeof p !== "object") return; // jsdom fetch stub 容错
    _bkBuiltinOpts = p.builtin_backup || [];
    _rsBuiltinOpts = p.builtin_restore || [];
    // 输入框预填「当前生效的完整参数」= 内置 + 已存默认
    _paramsCache.backup_opts = p.backup_opts || "";
    _paramsCache.restore_opts = p.restore_opts || "";
    $("#bk-extra-opts").value = _bkBuiltinOpts.concat(_splitOpts(_paramsCache.backup_opts)).join(" ");
    $("#rs-extra-opts").value = _rsBuiltinOpts.concat(_splitOpts(_paramsCache.restore_opts)).join(" ");
  } catch (e) { /* 参数面板加载失败不阻塞页面 */ }
}
function _wireOpts(kind, toggleId, rowId, inputId, saveBtnId, resetBtnId, settingsKey, builtinKey) {
  $ (toggleId).onclick = () => {
    const row = $(rowId);
    row.classList.toggle("hidden");
    $(toggleId).textContent = row.classList.contains("hidden") ? "▸ 高级参数" : "▾ 高级参数";
  };
  $(saveBtnId).onclick = async () => {
    try {
      // 存默认 = 输入框全文(完整参数清单),执行时整体替换内置
      const text = $(inputId).value.trim();
      await put("/api/settings", { [settingsKey]: text });
      _paramsCache[settingsKey] = text;
      toast("已存为默认参数", true);
    } catch (e) { toast("保存失败: " + e.message, false); }
  };
  $(resetBtnId).onclick = () => {
    const builtin = builtinKey === "backup" ? _bkBuiltinOpts : _rsBuiltinOpts;
    $(inputId).value = builtin.join(" ");
    _paramsCache[settingsKey] = "";
  };
}

$("#btn-backup").onclick = async () => {
  const scope = document.querySelector('input[name="bk-scope"]:checked').value;
  const dbs = scope === "pick" ? [...$("#bk-db-pick").selectedOptions].map((o) => o.value) : [];
  const dir = $("#bk-dir").value.trim();
  const gzip = $("#bk-gzip").checked;
  const extra = _splitOpts($("#bk-extra-opts").value);
  try {
    const r = await post("/api/backup", { dbs, backup_dir: dir, gzip, extra_opts: extra });
    if (!r.task_id) throw new Error(r.error || "未返回任务 ID");
    showProgressModal("备份执行中");
    pollTask(r.task_id);
  } catch (e) { toast("备份启动失败: " + e.message, false); }
};

let rsStorage = "local";   // restore file 位置: local(本地文件) / remote(远程服务器文件)
window.restoreRemote = function (el) {
  $("#rs-file").value = el.dataset.path || "";
  rsStorage = "remote";
  toast("已选择远程还原文件，将从远程服务器流式还原到目标库");
  switchPage("backup");
  const rs = document.getElementById("rs-file");
  if (rs && rs.scrollIntoView) rs.scrollIntoView({ behavior: "smooth", block: "center" });
};
$("#btn-restore").onclick = async () => {
  const target = $("#rs-target-db").value;
  const file = $("#rs-file").value.trim();
  if (!file) { toast("请先选择还原文件", false); return; }
  const ok = await confirmDialog("执行还原",
    `目标数据库: <b>${esc(target || "(使用文件自带建库)")}</b><br>${rsStorage === "remote" ? "还原文件(远程): <b>" : "还原文件(本地): <b>"}${esc(file)}</b><br><br>此操作将覆盖目标库中的同名表,<span style="color:var(--danger)">且不可撤销</span>。建议先执行备份。`);
  if (!ok) return;
  const extra = _splitOpts($("#rs-extra-opts").value);
  try {
    const r = await post("/api/restore", { target_db: target, file, extra_opts: extra, storage: rsStorage });
    if (!r.task_id) throw new Error(r.error || "未返回任务 ID");
    showProgressModal("还原执行中");
    pollTask(r.task_id);
  } catch (e) { toast("还原启动失败: " + e.message, false); }
};
_wireOpts("backup", "#bk-opts-toggle", "#bk-opts-row", "#bk-extra-opts",
          "#btn-bk-opts-save", "#btn-bk-opts-reset", "backup_opts", "backup");
_wireOpts("restore", "#rs-opts-toggle", "#rs-opts-row", "#rs-extra-opts",
          "#btn-rs-opts-save", "#btn-rs-opts-reset", "restore_opts", "restore");
initBackupParams();

/* ---------- 远程还原:选择远程服务器上的备份文件 ---------- */
async function loadRemoteRestoreUI() {
  const active = (connList || []).find((c) => c.active);
  const remote = active && !isLocalHost(active.host);
  const remoteBox = document.getElementById("rs-remote-file-box");
  const localBox = document.getElementById("rs-local-file-box");
  if (!remoteBox || !localBox) return;
  if (!remote) {
    remoteBox.classList.add("hidden");
    localBox.classList.remove("hidden");
    return;
  }
  remoteBox.classList.remove("hidden");
  localBox.classList.add("hidden");
  const dirInput = document.getElementById("rs-remote-dir");
  if (dirInput && !dirInput.value.trim() && active.remote_backup_dir) {
    dirInput.value = active.remote_backup_dir;
  }
  await loadRemoteRestoreFiles(active);
}

async function loadRemoteRestoreFiles(active) {
  const sel = document.getElementById("rs-remote-file");
  const hint = document.getElementById("rs-remote-hint");
  if (!sel) return;
  const dirInput = document.getElementById("rs-remote-dir");
  const dir = (dirInput && dirInput.value.trim()) || "";
  sel.innerHTML = '<option value="">(加载中...)</option>';
  try {
    const body = { conn_id: active.id };
    if (dir) body.dir = dir;
    const r = await post("/api/backup-files/remote", body);
    if (!r.ok) throw new Error(r.error || "加载失败");
    if (hint) hint.textContent = "远程目录: " + r.dir;
    if (!r.files || !r.files.length) {
      sel.innerHTML = '<option value="">(目录下暂无 .sql/.sql.gz 文件)</option>';
      return;
    }
    sel.innerHTML = '<option value="">请选择远程备份文件</option>' + r.files.map((f) =>
      `<option value="${esc(f.path)}">${esc(f.name)} (${fmtSize(f.size)}, ${esc(f.mtime)})</option>`).join("");
  } catch (e) {
    sel.innerHTML = '<option value="">(加载失败)</option>';
    if (hint) hint.textContent = e.message;
    toast(e.message, false);
  }
}

$("#rs-remote-file").addEventListener("change", (e) => {
  const v = e.target.value;
  if (v) { $("#rs-file").value = v; rsStorage = "remote"; }
});
$("#btn-rs-remote-refresh").onclick = () => {
  const active = (connList || []).find((c) => c.active);
  if (active) loadRemoteRestoreFiles(active);
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
    html += (d.dirs || []).map((x) => `<div class="b-item dir" data-path="${esc(x.path)}" onclick="window.bNav(this,'${box}')">${ICON.folder}<span class="b-name">${esc(x.name)}</span></div>`).join("");
    html += (d.files || []).map((f) => `<div class="b-item file" data-path="${esc(f.path)}" onclick="window.bPickFile(this)">${ICON.file}<span class="b-name">${esc(f.name)}</span><span class="b-size">${fmtSize(f.size)}</span></div>`).join("");
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
function pickFile(p) { $("#rs-file").value = p; rsStorage = "local"; $("#rs-file-browser").classList.add("hidden"); }
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
    if (r.path) { $("#rs-file").value = r.path; rsStorage = "local"; $("#rs-file-browser").classList.add("hidden"); }
  } catch (e) { toast("选择失败: " + e.message, false); }
};

/* ---------- 定时备份(多任务) ---------- */
let scEnv = null;
let scEditingId = null;
let scOrigEnabled = true;

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
  scOrigEnabled = true;
  $("#sc-modal-title").textContent = tid ? "编辑定时备份任务" : "新建定时备份任务";
  $("#sf-name").value = "";
  $("#sf-freq").value = "daily";
  $("#sf-interval").value = 1; $("#sf-weekday").value = 0; $("#sf-day").value = 1;
  $("#sf-time").value = "00:00"; $("#sf-once").value = ""; $("#sf-keep").value = 7;
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
        scOrigEnabled = !!t.enabled;
        $("#sf-name").value = t.name;
        $("#sf-freq").value = t.freq;
        $("#sf-interval").value = t.interval_hours || 1;
        $("#sf-weekday").value = t.weekday == null ? 0 : t.weekday;
        $("#sf-day").value = t.day_of_month || 1;
        $("#sf-time").value = t.time || "00:00";
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
    time: $("#sf-time").value || "00:00",
    enabled: scEditingId ? scOrigEnabled : true,
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

/* ---------- SQL 查询页(多页签) ---------- */
let _qTabs = [];           // [{id,label,sql,db,running,pid,result(结果对象或null),truncated}]
let _qActive = -1;         // 当前激活页签 index
let _qSeq = 0;             // 页签自增 id
let _qDbs = [];            // 当前激活连接的库列表

async function loadQueryPage() {
  if (!_qTabs.length) addQueryTab();       // 首次进入,建一个默认页签
  renderQueryTabs();
  await loadQueryMaxRows();                 // 读取查询行数上限并更新提示
  await loadQueryDbs();                     // 加载库下拉(仅其源与连接相关,供各页签使用)
}

let _queryMaxRows = "500";
async function loadQueryMaxRows() {
  try {
    const s = await get("/api/settings") || {};
    if (s.query_max_rows != null) _queryMaxRows = String(s.query_max_rows);
  } catch (e) { /* 读取失败沿用默认 */ }
  updateQueryMaxHint(_queryMaxRows);
}
function updateQueryMaxHint(v) {
  const h = $("#query-max-hint");
  if (h) h.textContent = `仅只读查询 · 结果最多返回 ${v} 行`;
  const qt = $("#query-truncated");
  if (qt && qt.classList.contains("hidden") === false) qt.textContent = `结果已截断,仅显示前 ${v} 行`;
}

async function loadQueryDbs() {
  try {
    _qDbs = await get("/api/databases") || [];
  } catch (e) { _qDbs = []; }
  const sel = $("#query-db-select");
  if (!sel) return;
  const cur = sel.value;
  sel.innerHTML = '<option value="">不使用数据库（需表名带库前缀）</option>' +
    _qDbs.map((d) => `<option value="${esc(d.name)}">${esc(d.name)}${d.table_count ? ` (${d.table_count} 表)` : ""}</option>`).join("");
  if (_qActive >= 0 && _qTabs[_qActive]) {
    const t = _qTabs[_qActive];
    if (t.db && _qDbs.some((d) => d.name === t.db)) sel.value = t.db;
  }
}

function currentQueryTab() { return (_qActive >= 0 && _qTabs[_qActive]) ? _qTabs[_qActive] : null; }

function addQueryTab() {
  _qSeq++;
  _qTabs.push({ id: _qSeq, label: "查询 " + _qSeq, sql: "", db: "", running: false, pid: null, result: null, truncated: false });
  _qActive = _qTabs.length - 1;
  renderQueryTabs();
  mountActiveTab();
}

function renderQueryTabs() {
  const bar = $("#query-tabs");
  if (!bar) return;
  bar.innerHTML = _qTabs.map((t, i) => `
    <div class="q-tab ${i === _qActive ? "active" : ""}" data-i="${i}">
      <span class="q-tab-title" data-i="${i}">${esc(t.label)}</span>
      <span class="q-tab-close" data-i="${i}" title="关闭">×</span>
    </div>`).join("") +
    '<button class="q-tab-add" id="btn-add-query-tab" title="新建页签">＋</button>';
  // 事件委托:切换 / 关闭
  $$("#query-tabs [data-i]").forEach((el) => {
    el.onclick = (ev) => {
      const idx = parseInt(el.dataset.i, 10);
      if (el.classList.contains("q-tab-close")) { closeQueryTab(idx); return; }
      switchQueryTab(idx);
    };
  });
  const add = $("#btn-add-query-tab");
  if (add) add.onclick = addQueryTab;
}

function closeQueryTab(idx) {
  if (_qTabs.length <= 1) { toast("至少保留一个页签", false); return; }
  if (_qTabs[idx].running) { toast("该页签正在执行查询,请先等待或终止", false); return; }
  _qTabs.splice(idx, 1);
  if (_qActive >= idx) _qActive = Math.max(0, _qActive - 1);
  if (_qActive > _qTabs.length - 1) _qActive = _qTabs.length - 1;
  renderQueryTabs();
  mountActiveTab();
}

function switchQueryTab(idx) {
  if (idx === _qActive) return;
  saveActiveTabToState();
  _qActive = idx;
  renderQueryTabs();
  mountActiveTab();
}

/* 把当前 DOM 编辑器/库选择写回状态(切页签/执行前调用) */
function saveActiveTabToState() {
  const t = currentQueryTab();
  if (!t) return;
  t.sql = $("#query-editor") ? $("#query-editor").value : t.sql;
  t.db = $("#query-db-select") ? $("#query-db-select").value : t.db;
}

/* 把目标页签状态装载到唯一 DOM(编辑器/库/结果) */
function mountActiveTab() {
  const t = currentQueryTab();
  if (!t) return;
  const editor = $("#query-editor");
  if (editor) editor.value = t.sql;
  const sel = $("#query-db-select");
  if (sel) sel.value = t.db;
  // 恢复该页签的结果
  renderQueryTable(t.result);
  const qt = $("#query-truncated");
  qt.classList.toggle("hidden", !t.truncated);
  if (t.truncated) qt.textContent = `结果已截断,仅显示前 ${_queryMaxRows} 行`;
  $("#btn-kill-query").classList.toggle("hidden", !t.running);
}

/* 渲染结果:r = {columns,rows,truncated,affected,elapsed,db}|null */
function renderQueryTable(r) {
  const head = $("#query-result-head");
  const body = $("#query-result-body");
  const meta = $("#query-meta");
  if (!r || !head || !body) return;
  const cols = r.columns || [];
  head.innerHTML = cols.length
    ? `<tr>${cols.map((c) => `<th>${esc(String(c))}</th>`).join("")}</tr>`
    : "";
  body.innerHTML = (r.rows || []).map((row) => {
    const cells = Array.isArray(row) ? row : cols.map((c) => row && row[c]);
    return `<tr>${cells.map((v) => {
      if (v === null || v === undefined) return `<td class="tbl-null">NULL</td>`;
      return `<td title="${esc(String(v))}">${esc(String(v))}</td>`;
    }).join("")}</tr>`;
  }).join("") || "";
  if (meta) {
    const rows = (r.rows || []).length;
    let info = `耗时 ${r.elapsed != null ? r.elapsed + "s" : "—"} · ${rows} 行`;
    if (cols.length) info += ` · ${cols.length} 列`;
    if (r.affected) info += ` · 影响 ${r.affected} 行`;
    if (r.db) info += ` · 数据库 ${r.db}`;
    meta.textContent = info;
  }
}

async function runQuery() {
  saveActiveTabToState();
  const t = currentQueryTab();
  if (!t) return;
  const sql = t.sql.trim();
  if (!sql) { toast("请输入 SQL 语句", false); return; }
  t.running = true; t.pid = null; t.result = null; t.truncated = false;
  $("#btn-kill-query").classList.remove("hidden");
  const status = $("#query-status");
  if (status) status.textContent = "执行中…";
  try {
    const r = await post("/api/query", { sql, db: t.db || "", max_rows: parseInt(_queryMaxRows, 10) || undefined });
    if (r && r.ok) {
      t.pid = r.pid || null;
      t.result = { columns: r.columns || [], rows: r.rows || [], affected: r.affected,
                   elapsed: r.elapsed, db: r.db };
      t.truncated = !!r.truncated;
    } else if (r) {
      toast(r.killed ? "查询已被终止" : (r.error || "查询失败"), false);
      const meta = $("#query-meta");
      if (meta) meta.textContent = r.error || "查询失败";
      $("#query-result-head").innerHTML = "";
      $("#query-result-body").innerHTML = "";
    }
  } catch (e) {
    toast(e.message || "查询失败", false);
    const meta = $("#query-meta");
    if (meta) meta.textContent = e.message || "查询失败";
    $("#query-result-head").innerHTML = "";
    $("#query-result-body").innerHTML = "";
  } finally {
    t.running = false;
    $("#btn-kill-query").classList.add("hidden");
    if (status) status.textContent = "";
    mountActiveTab();   // 重新装载(更新结果展示)
  }
}

async function killQuery() {
  const t = currentQueryTab();
  if (!t || !t.running) return;
  if (!t.pid) { toast("暂无查询连接可终止", false); return; }
  try {
    await post("/api/query/kill", { pid: t.pid });
    toast("已发送终止信号", true);
  } catch (e) { toast(e.message || "终止失败", false); }
}

$("#btn-run-query").onclick = runQuery;
$("#btn-kill-query").onclick = killQuery;
$("#btn-refresh-dbs").onclick = loadQueryDbs;
// 编辑期间即时同步到状态(切换页签时已 saveActiveTabToState,此处只在离开页面前兜底)
$("#query-editor").addEventListener("blur", () => saveActiveTabToState());
// Ctrl+Enter 快捷执行
$("#query-editor").addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") runQuery();
});

/* ---------- AI 助手侧栏(查询页) ---------- */
let _aiOpen = false;
const _aiHistory = [];   // 本会话消息记录(用于上下文,可选)

function aiToggle(force) {
  _aiOpen = force !== undefined ? force : !_aiOpen;
  const sb = $("#ai-sidebar");
  if (sb) sb.classList.toggle("hidden", !_aiOpen);
  if (_aiOpen && sb) {
    if (!sb.querySelector(".ai-msg")) aiAddMsg("ai", "你好,我是 MySQL AI 助手。可帮我做:\n• 自然语言转 SQL(会带上当前库的表结构)\n• 分析 SQL 性能(结合 EXPLAIN)\n• 生成告警 / 健康报告摘要\n\n请先在「系统设置 → AI 设置」配置 API。");
  }
}
$("#btn-toggle-ai").onclick = () => aiToggle();
$("#btn-close-ai").onclick = () => aiToggle(false);
$("#btn-ai-send").onclick = () => aiSend();

function aiAddMsg(role, text, extra) {
  const body = $("#ai-body");
  if (!body) return;
  const div = document.createElement("div");
  div.className = "ai-msg " + role;
  if (role === "ai" && extra && extra.actions) {
    const t = document.createElement("span");
    t.textContent = text;
    div.appendChild(t);
    const acts = document.createElement("div");
    acts.className = "ai-actions";
    extra.actions.forEach((a) => {
      const b = document.createElement("button");
      b.className = "btn btn-sm";
      b.textContent = a.label;
      b.onclick = a.fn;
      acts.appendChild(b);
    });
    div.appendChild(acts);
  } else {
    div.textContent = text;
  }
  body.appendChild(div);
  body.scrollTop = body.scrollHeight;
}

function aiSetStatus(msg, isErr) {
  const st = $("#ai-status");
  if (st) { st.textContent = msg || ""; st.style.color = isErr ? "var(--danger, #d33)" : ""; }
}

function aiCurrentDb() {
  const sel = $("#query-db-select");
  return sel ? sel.value : "";
}

async function aiSend() {
  const input = $("#ai-input");
  const text = (input && input.value || "").trim();
  if (!text) return;
  if (input) input.value = "";
  aiAddMsg("user", text);
  aiSetStatus("思考中…");
  const db = aiCurrentDb();
  try {
    const r = await post("/api/ai/sql-gen", { prompt: text, db });
    if (r && r.unconfigured) {
      aiSetStatus("AI 未配置", true);
      aiAddMsg("err", r.error || "AI 功能未配置");
      return;
    }
    if (r && r.ok && r.sql) {
      const t = currentQueryTab();
      aiAddMsg("ai", r.sql, {
        actions: [
          { label: "插入查询框", fn: () => { if (t) { t.sql = r.sql; saveActiveTabToState(); mountActiveTab(); } } },
          { label: "执行", fn: () => { if (t) { t.sql = r.sql; saveActiveTabToState(); mountActiveTab(); runQuery(); } } },
        ],
      });
      aiSetStatus("");
    } else {
      aiAddMsg("err", (r && r.error) || "生成失败");
      aiSetStatus("");
    }
  } catch (e) {
    aiSetStatus("");
    aiAddMsg("err", e.message || "请求失败");
  }
}

/* AI 分析当前查询(SQL + EXPLAIN) */
async function aiAnalyze() {
  const t = currentQueryTab();
  if (!t || !t.sql.trim()) { toast("请先输入 SQL", false); return; }
  aiToggle(true);
  aiAddMsg("user", "分析以下 SQL 性能:\n" + t.sql);
  aiSetStatus("分析中…");
  let explainRows = [];
  try {
    const er = await post("/api/query", { sql: "EXPLAIN " + t.sql.replace(/^\s*;?\s*/, ""), db: t.db || "", max_rows: 50 });
    if (er && er.ok) explainRows = er.rows || [];
    else if (er && er.error) explainRows = [["EXPLAIN 失败: " + er.error]];
  } catch (e) { explainRows = [["EXPLAIN 失败: " + (e.message || "")]]; }
  try {
    const r = await post("/api/ai/sql-analyze", { sql: t.sql, explain: explainRows, db: t.db || "" });
    if (r && r.ok && r.advice) { aiAddMsg("ai", r.advice); aiSetStatus(""); }
    else { aiAddMsg("err", (r && r.error) || "分析失败"); aiSetStatus(""); }
  } catch (e) { aiSetStatus(""); aiAddMsg("err", e.message || "请求失败"); }
}

/* 生成告警 / 健康报告摘要 */
async function aiReport(kind) {
  aiToggle(true);
  const label = kind === "alert" ? "告警周报" : "健康报告";
  aiAddMsg("user", "请生成" + label);
  aiSetStatus("生成中…");
  try {
    const r = await post("/api/ai/report", { type: kind });
    if (r && r.ok && r.report) { aiAddMsg("ai", r.report); aiSetStatus(""); }
    else { aiAddMsg("err", (r && r.error) || "生成失败"); aiSetStatus(""); }
  } catch (e) { aiSetStatus(""); aiAddMsg("err", e.message || "请求失败"); }
}

/* AI 配置:回填设置页字段 / 校验 / 保存 */
async function loadAiConfigFields() {
  const el = { en: $("#set-ai-enabled"), url: $("#set-ai-base-url"), key: $("#set-ai-key"), model: $("#set-ai-model") };
  if (!el.en) return;   // 系统设置页元素不存在(其它页调用时)则跳过
  try {
    const c = await get("/api/ai/config") || {};
    if (el.en) el.en.checked = !!c.enabled;
    if (el.url) el.url.value = c.base_url || "";
    if (el.model) el.model.value = c.model || "";
    // 不展示明文 key,仅提示是否已保存
    if (el.key) el.key.value = "";
    if (el.key) el.key.placeholder = c.has_key ? "已保存(留空 = 保持不变)" : "未配置(填写后保存)";
  } catch (e) { /* 忽略 */ }
}
$("#btn-test-ai").onclick = async () => {
  const st = $("#ai-config-status");
  const payload = {
    base_url: ($("#set-ai-base-url").value || "").trim(),
    api_key: ($("#set-ai-key").value || "").trim(),
    model: ($("#set-ai-model").value || "").trim(),
  };
  setStatus(st, "测试中...");
  try {
    const r = await post("/api/ai/test", payload);
    if (r && r.ok) setStatus(st, `✓ 连通 ${r.elapsed_ms}ms (model=${r.model})`, "ok");
    else setStatus(st, "✗ " + ((r && r.error) || "失败"), "err");
  } catch (e) { setStatus(st, "✗ " + e.message, "err"); }
};
$("#btn-save-ai-config").onclick = async () => {
  const st = $("#ai-config-status");
  const payload = {
    enabled: !!$("#set-ai-enabled").checked,
    base_url: ($("#set-ai-base-url").value || "").trim(),
    api_key: ($("#set-ai-key").value || "").trim(),
    model: ($("#set-ai-model").value || "").trim(),
  };
  setStatus(st, "保存中...");
  try {
    const r = await post("/api/ai/config", payload);
    if (r && r.ok) { setStatus(st, "AI 配置已保存", "ok"); }
    else { setStatus(st, (r && r.error) || "保存失败", false); }
  } catch (e) { setStatus(st, "保存失败: " + e.message, false); }
};

/* ---------- 系统设置 ---------- */
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
    tb.innerHTML = env.items.map((it, idx) => {
      // 前 3 项为必检项（Python/PyMySQL/cryptography），后 2 项为可选客户端工具
      const required = idx < 3;
      const st = it.ok
        ? { cls: "success", ico: ICON.ok, color: "var(--success)", txt: "通过" }
        : required
          ? { cls: "failed", ico: ICON.err, color: "var(--danger)", txt: "未通过" }
          : { cls: "running", ico: ICON.warn, color: "var(--warn)", txt: "缺失" };
      return `
      <tr>
        <td style="width:44px;text-align:center;"><span style="color:${st.color};display:inline-flex;vertical-align:middle;">${st.ico}</span></td>
        <td><b>${esc(it.name)}</b>${it.detail ? `<div class="hint">${esc(it.detail)}</div>` : ""}</td>
        <td style="white-space:nowrap;"><span class="badge ${st.cls}"><span class="bd-dot"></span>${st.txt}</span></td>
        <td class="hint">${it.ok ? "" : esc(it.tip)}</td>
      </tr>`;
    }).join("");
    $("#su-env-summary").textContent = env.all_required_ok
      ? "核心依赖齐备,可继续。MySQL 客户端缺失只影响备份/还原,可在下一步配置。"
      : "存在缺失项。Python/PyMySQL 缺失需在服务器端修复;客户端缺失可下一步手动指定。";
    // 内置 tools/ 检测:自动选中内置 bin 目录并提示,用户可直接下一步或快速跳过
    const bd = document.getElementById("su-bundled-hint");
    if (bd) {
      if (env.bundled_tools && env.bundled_tools.length) {
        const first = env.bundled_tools[0];
        if (!$("#su-mysql-bin").value) $("#su-mysql-bin").value = first.dir;
        const vers = env.bundled_tools.map((t) => t.version || "未知版本").join("、");
        bd.innerHTML = `已检测到<b>随程序内置的 MySQL 客户端</b>(${esc(vers)}),已自动选中 ${esc(first.dir)}。内置多个版本时,可在连接中按数据库版本(5.7/8.x)自动匹配对应工具。如不使用可直接跳过本步。`;
        bd.classList.remove("hidden");
      } else {
        bd.classList.add("hidden");
      }
    }
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
$("#su-btn-download-tools").onclick = async () => {
  const st = $("#su-probe-status");
  setStatus(st, "下载中（5.7+8.x，约数百MB，请稍候）...");
  try {
    const r = await post("/api/setup/download-tools", {});
    if (!r || !r.ok) { setStatus(st, "✗ " + ((r && r.error) || "下载失败"), "err"); return; }
    if (r.has_tools) { setStatus(st, "✓ " + r.message, "ok"); runEnvCheck(); return; }
    // 轮询后台下载状态（ponytail: 避免堵 240s，长下载走 poll）
    let tries = 0;
    const poll = async () => {
      tries++;
      try {
        const s = await api("/api/setup/download-tools/status");
        if (s.has_tools || s.status === "done") { setStatus(st, "✓ " + (s.msg || "下载完成"), "ok"); runEnvCheck(); return; }
        if (s.status === "failed") { setStatus(st, "✗ " + (s.error || s.msg || "下载失败"), "err"); return; }
        setStatus(st, (s.msg || "下载中") + " (" + tries*2 + "s)...");
      } catch(e) { setStatus(st, "✗ " + e.message, "err"); return; }
      if (tries < 150) setTimeout(poll, 2000); else setStatus(st, "✗ 下载超时，可跳过或手动指定目录", "err");
    };
    setTimeout(poll, 2000);
  } catch (e) { setStatus(st, "✗ " + e.message, "err"); }
};
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
  ensureDashboardCharts();
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
  // 健康评分趋势
  try {
    const h = await get("/api/dashboard/health-history");
    const data = (h.points || []).map((p) => [p.t * 1000, p.score]);
    charts.healthTrend.setOption({ series: [{ data }] });
  } catch (e) {}
  // InnoDB
  try {
    const m = await get("/api/dashboard/innodb");
    $("#innodb-hit-rate").textContent = m.hit_rate;
    $("#innodb-rows-read").textContent = fmtNum(m.rows_read);
    $("#innodb-rows-write").textContent = fmtNum(m.rows_inserted + m.rows_updated + m.rows_deleted);
    $("#innodb-lock-waits").textContent = fmtNum(m.lock_waits);
  } catch (e) {}
  // 表空间 Top 10(横向条形图)
  try {
    const ts = await get("/api/dashboard/tablespace") || [];
    const data = ts.map((t) => ({ value: t.total_size, name: `${t.db}.${t.name}`, rows: t.rows }));
    const empty = !data.length;
    charts.tsBar.setOption({
      series: [{ data }],
      graphic: { elements: empty ? [{ id: "ts-empty", type: "text", left: "center", top: "middle", style: { text: "暂无数据", fill: cssVar("--text-3") || "#8a93a6", fontSize: 13 } }] : [{ id: "ts-empty", $action: "remove" }] },
    });
  } catch (e) {}
  // 数据库空间占比(环形图)
  try {
    const dbs = await get("/api/databases") || [];
    const sorted = dbs.slice().sort((a, b) => (b.total_size || 0) - (a.total_size || 0));
    const data = sorted.slice(0, 7).map((d) => ({ name: d.name, value: d.total_size || 0 }));
    const restSum = sorted.slice(7).reduce((s, d) => s + (d.total_size || 0), 0);
    if (restSum > 0) data.push({ name: "其他", value: restSum });
    const empty = !data.length;
    const total = sorted.reduce((s, d) => s + (d.total_size || 0), 0);
    // 环形中心展示总空间,占比悬殊时也能一眼读出规模
    const elements = [
      { id: "donut-total", type: "text", left: "center", top: "42%",
        style: {
          text: `{v|${fmtSize(total)}}\n{l|总空间}`,
          textAlign: "center",
          rich: {
            v: { fontSize: 17, fontWeight: 600, fill: cssVar("--text") || "#1a2233", lineHeight: 22 },
            l: { fontSize: 11, fill: cssVar("--text-3") || "#8a93a6", lineHeight: 16 },
          },
        } },
      ...(empty
        ? [{ id: "donut-empty", type: "text", left: "center", top: "middle", style: { text: "暂无数据", fill: cssVar("--text-3") || "#8a93a6", fontSize: 13 } }]
        : [{ id: "donut-empty", $action: "remove" }]),
    ];
    charts.dbDonut.setOption({ series: [{ data }], graphic: { elements } });
  } catch (e) {}
  // 复制
  try {
    const r = await get("/api/dashboard/replication");
    const el = $("#replication-status");
    if (!r.is_slave) {
      el.innerHTML = `<div class="hint" style="display:flex;align-items:center;gap:8px;color:var(--text-3);">${ICON.info}<span>${esc(r.message)}</span></div>`;
    } else {
      const ioOk = r.io_running === "Yes";
      const sqlOk = r.sql_running === "Yes";
      el.innerHTML = `
        <div class="info-row"><span class="info-label">IO 线程</span><span class="info-value ${ioOk ? "ok" : "err"}">${ioOk ? "运行中" : "已停止"}</span></div>
        <div class="info-row"><span class="info-label">SQL 线程</span><span class="info-value ${sqlOk ? "ok" : "err"}">${sqlOk ? "运行中" : "已停止"}</span></div>
        <div class="info-row"><span class="info-label">主库</span><span class="info-value">${esc(r.master_host)}</span></div>
        <div class="info-row"><span class="info-label">延迟</span><span class="info-value ${r.seconds_behind === "0" ? "ok" : "warn"}">${r.seconds_behind} 秒</span></div>
        ${r.last_error ? `<div class="info-row"><span class="info-label">错误</span><span class="info-value err">${esc(r.last_error)}</span></div>` : ""}`;
    }
  } catch (e) {}
  // 刷新按钮
  const btn = $("#btn-refresh-health");
  if (btn) btn.onclick = loadDashboardPage;
  /* 看板页由 display:none 变为可见后才真正定尺寸;若 echarts.init 时尺寸为 0,
     setOption 只会更新数据模型而跳过渲染(环形图/柱状图停在空白/灰环状态)。
     数据全部提交后统一 resize 一次,强制按最新数据重绘。 */
  nextFrame(() => {
    ["healthTrend", "dbDonut", "tsBar"].forEach((k) => {
      const c = charts[k];
      if (c && c.resize) c.resize();
    });
  });
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
let _alertDays = 1;
async function loadAlertsPage() {
  ensureAlertsCharts();
  try {
    const r = await get("/api/alerts");
    const content = $("#alerts-content");
    if (!r.alerts || r.alerts.length === 0) {
      content.innerHTML = '<div class="empty"><div style="color:var(--success);display:inline-flex;width:44px;height:44px;border-radius:50%;background:var(--success-bg);align-items:center;justify-content:center;margin-bottom:10px;">' + ICON.ok + '</div><div class="e-title">当前无告警</div><div class="hint">所有指标均在阈值范围内</div></div>';
    } else {
      content.innerHTML = r.alerts.map((a) => {
        const critical = a.level === "critical";
        return `<div class="alert-item ${critical ? "critical" : "warning"}">${critical ? ICON.warn : ICON.bell}<span><b>${critical ? "严重" : "警告"}</b> ${esc(a.message)}</span></div>`;
      }).join("");
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
  // 告警历史趋势
  try {
    const r = await get("/api/alerts/history?days=" + _alertDays);
    const warn = [], crit = [];
    (r.points || []).forEach((p) => {
      const t = p.t * 1000;
      warn.push([t, p.warning || 0]);
      crit.push([t, p.critical || 0]);
    });
    charts.alertHist.setOption({ series: [{ data: warn }, { data: crit }] });
    /* 告警页初始 hidden,echarts.init 时容器尺寸可能为 0,setOption 只更新模型不重绘;
       数据提交后 resize 一次,强制按最新数据绘制(与数据看板修复一致)。 */
    nextFrame(() => { if (charts.alertHist && charts.alertHist.resize) charts.alertHist.resize(); });
    const up = $("#alert-history-updated");
    if (up) up.textContent = r.updated_at ? `采样至 ${r.updated_at} · 近 ${_alertDays} 天` : "暂无采样数据(启用连接后每 1 分钟自动记录)";
  } catch (e) {}
}
/* 告警趋势时间范围切换(事件委托,只绑定一次) */
$("#alert-range-seg").addEventListener("click", (e) => {
  const b = e.target.closest(".seg-btn");
  if (!b) return;
  $$("#alert-range-seg .seg-btn").forEach((x) => x.classList.toggle("active", x === b));
  _alertDays = parseInt(b.dataset.days, 10);
  loadAlertsPage();
});
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
    const qmr = $("#set-query-max-rows");
    if (qmr && s.query_max_rows != null) qmr.value = s.query_max_rows;
    loadAiConfigFields();
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
$("#btn-save-query-max-rows").onclick = async () => {
  const st = $("#set-query-max-rows-status");
  const val = parseInt($("#set-query-max-rows").value, 10);
  if (!Number.isFinite(val) || val < 1) { setStatus(st, "请输入 ≥1 的有效数值", false); return; }
  try {
    await put("/api/settings", { query_max_rows: val });
    setStatus(st, "已保存", "ok");
    updateQueryMaxHint(val);
  } catch (e) { setStatus(st, "保存失败: " + e.message, false); }
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
  if (!(await confirmDialog("切换到全量模式", "切换后不可逆,确认继续?<br>已有数据将统一迁移到 MySQL 系统库。"))) return;
  setStatus(st, "切换中...");
  try {
    await post("/api/switch-to-full-mode", { sys_db_name: sysDb, admin_user: adminUser, admin_pass: adminPass });
    setStatus(st, "切换成功，正在刷新...", "ok");
    setTimeout(() => { location.reload(); }, 1000);
  } catch (e) { setStatus(st, e.message, "err"); }
};

/* ---------- 主题切换（浅色/暗色） ---------- */
const THEME_KEY = "mc_theme";
function applyTheme(t) {
  document.documentElement.setAttribute("data-theme", t);
  try { localStorage.setItem(THEME_KEY, t); } catch (e) {}
  const sun = $("#ico-sun"), moon = $("#ico-moon");
  if (sun) sun.style.display = t === "dark" ? "none" : "";
  if (moon) moon.style.display = t === "dark" ? "" : "none";
  if (typeof refreshChartColors === "function") refreshChartColors();
}
$("#btn-theme").onclick = () => applyTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark");

/* ---------- 初始化 ---------- */
document.querySelectorAll(".nav-item").forEach((el) => {
  el.onclick = () => switchPage(el.dataset.page);
});
document.querySelectorAll("[data-goto]").forEach((el) => {
  el.onclick = () => switchPage(el.dataset.goto);
});

window.addEventListener("resize", () => {
  Object.values(charts).forEach((c) => { if (c && c.resize) c.resize(); });
});

/* ---------- 大屏只读模式 (?mode=fullscreen) ---------- */
let _fsTimer = null;
function updateFsConn() {
  const el = $("#fs-conn");
  if (!el) return;
  const conn = connList.find((c) => c.active) || connList.find((c) => c.id === $("#conn-select").value);
  if (!conn) { el.textContent = "未连接"; el.className = "fs-conn off"; return; }
  el.textContent = `${conn.name} (${conn.host}:${conn.port}) · 已连接`;
  el.className = "fs-conn ok";
}
function enterFullscreenMode() {
  document.body.classList.add("fullscreen");
  // 注入大屏头部(品牌 + 连接状态 + 时钟 + 退出),仅注入一次
  const content = document.querySelector(".content");
  if (content && !$("#fs-header")) {
    const h = document.createElement("div");
    h.id = "fs-header";
    h.innerHTML = `
      <div class="fs-brand">
        <div class="fs-mark"><svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="6" rx="7" ry="2.5"/><path d="M5 6v12a7 2.5 0 0 0 14 0V6"/><path d="M5 12a7 2.5 0 0 0 14 0"/></svg></div>
        <div><div class="fs-title">MySQL Console · 运行大屏</div><div class="fs-sub">只读模式 · 每 30 秒自动刷新</div></div>
      </div>
      <div class="fs-right">
        <span class="fs-conn off" id="fs-conn">未连接</span>
        <span class="fs-clock" id="fs-clock">--:--:--</span>
        <button class="fs-exit" id="fs-exit">退出大屏</button>
      </div>`;
    content.insertBefore(h, content.firstChild);
    const tick = () => { const c = $("#fs-clock"); if (c) c.textContent = new Date().toLocaleTimeString("zh-CN", { hour12: false }); };
    tick(); setInterval(tick, 1000);
    $("#fs-exit").onclick = () => { location.href = location.pathname + location.search.replace(/[?&]mode=fullscreen/, "").replace(/^&/, "?"); };
  }
  // 只显示数据看板页(大屏主视图),其余页面隐藏
  $$(".page").forEach((p) => p.classList.add("hidden"));
  const d = $("#page-dashboard");
  if (d) d.classList.remove("hidden");
  updateFsConn();
  loadDashboardPage();
  // 每 30 秒自动刷新看板数据
  clearInterval(_fsTimer);
  _fsTimer = setInterval(() => { loadDashboardPage(); updateFsConn(); }, 30000);
}

async function init() {
  // 应用已保存的主题
  let savedTheme = "light";
  try { savedTheme = localStorage.getItem(THEME_KEY) || "light"; } catch (e) {}
  applyTheme(savedTheme);
  initCharts();
  addChartExport(".chart-box");
  const fsMode = new URLSearchParams(location.search).get("mode") === "fullscreen";
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
    if (fsMode) { enterFullscreenMode(); } else { switchPage("overview"); }
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
    const pf = $("#info-platform");
    const is = $("#info-update-state");
    // 运行平台：优先 /api/version 的 platform
    if (pf) {
      try {
        const v = await get("/api/version");
        const pl = (v && v.platform) || "—";
        pf.textContent = pl === "windows" ? "Windows" : pl === "linux" ? "Linux" : pl;
      } catch (e) { pf.textContent = "—"; }
    }
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
    // 无条件展示最新版本更新日志(offline 时如实提示)
    const ll = $("#up-latest-log");
    if (ll) ll.textContent = (r && r.body) ? (r.body.slice(0, 1500)) : ((r && r.offline) ? "无法连接 GitHub(离线),更新日志暂不可用" : "暂无更新日志");
    if (r && r.has_update) {
      $("#up-result").textContent = "发现新版本 v" + r.latest;
      $("#up-result").className = "hint";
      $("#up-actions").classList.remove("hidden");
      /* 更新日志只在上方 #up-latest-log 展示一次,不再写入 #up-changelog(避免重复显示) */
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
    const ll = $("#up-latest-log");
    if (ll) ll.textContent = (r.body) ? (r.body.slice(0, 1500)) : ((r.offline) ? "无法连接 GitHub(离线),更新日志暂不可用" : "暂无更新日志");
    if (r.has_update) {
      st.textContent = "发现新版本 v" + r.latest;
      $("#up-latest").textContent = "v" + r.latest;
      $("#up-actions").classList.remove("hidden");
      /* 更新日志只在上方 #up-latest-log 展示一次 */
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
