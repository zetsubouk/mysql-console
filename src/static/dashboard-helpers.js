/* 数据看板纯逻辑 —— 生产唯一实现(index.html 以 <script defer> 在 app.js 之前加载)。
 * 历史:本文件曾是 ESM「影子副本」——生产从未加载它,vitest 测的是它,而 app.js 里
 * 跑的是手抄份,测试绿不代表生产对。2026-09-10 收敛为唯一实现:app.js 经
 * window.DashHelpers 薄封装调用(vitest 直接断言本文件 + 防漂移断言)。
 * 约定:全部纯函数,状态由参数传入,不依赖 DOM/echarts;新增看板纯逻辑放这里。 */
(function (global) {
  "use strict";

  /* 健康分趋势降采样:等距分桶取均值,保留桶中位点时间(移植自 app.js 原实现,行为不变) */
  function downsample(points, maxPoints) {
    maxPoints = maxPoints || 400;
    if (!Array.isArray(points) || points.length <= maxPoints) return points || [];
    const bucket = Math.ceil(points.length / maxPoints);
    const out = [];
    for (let i = 0; i < points.length; i += bucket) {
      const slice = points.slice(i, i + bucket);
      const avg = slice.reduce((s, p) => s + (Number(p.score) || 0), 0) / slice.length;
      out.push({ t: slice[Math.floor(slice.length / 2)].t, score: Math.round(avg * 10) / 10 });
    }
    return out;
  }

  /* 「最后更新 hh:mm:ss」提示文案 */
  function formatUpdated(ts) {
    if (!ts) return "最后更新 --";
    const d = new Date(ts);
    const hh = String(d.getHours()).padStart(2, "0");
    const mm = String(d.getMinutes()).padStart(2, "0");
    const ss = String(d.getSeconds()).padStart(2, "0");
    return "最后更新 " + (d.getMonth() + 1) + "-" + d.getDate() + " " + hh + ":" + mm + ":" + ss;
  }

  /* 实时状态:<30s live / <90s stale / 其余 offline;lastOk=0 视为从未成功 */
  function status(lastOk, now) {
    now = now || Date.now();
    if (!lastOk) return "offline";
    const age = now - lastOk;
    if (age < 30000) return "live";
    if (age < 90000) return "stale";
    return "offline";
  }

  global.DashHelpers = { downsample: downsample, formatUpdated: formatUpdated, status: status };
})(typeof window !== "undefined" ? window : this);
