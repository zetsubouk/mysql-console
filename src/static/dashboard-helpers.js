export function downsampleHealth(points, maxPoints = 400) {
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

export function filterTablespaceByDb(list, dbName) {
  if (!dbName || dbName === "其他") return list || [];
  return (list || []).filter((t) => t.db === dbName);
}

export function computeDashboardStatus(lastOkMs, nowMs = Date.now()) {
  if (!lastOkMs) return "offline";
  const age = nowMs - lastOkMs;
  if (age < 30000) return "live";
  if (age < 90000) return "stale";
  return "offline";
}

export function formatUpdatedHint(tsMs) {
  if (!tsMs) return "最后更新 --";
  const d = new Date(tsMs);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  const ss = String(d.getSeconds()).padStart(2, "0");
  return `最后更新 ${d.getMonth() + 1}-${d.getDate()} ${hh}:${mm}:${ss}`;
}

export function sliceByHours(points, hours) {
  if (!hours) return points || [];
  const cutoff = Date.now() / 1000 - hours * 3600;
  return (points || []).filter((p) => p.t >= cutoff);
}

export function ariaLabelForChart(id, title) {
  const map = {
    "chart-health-trend": `${title}，时间轴折线，含 75 警戒与 60 较差参考线，支持键盘聚焦与导出`,
    "chart-db-donut": `${title}，环形占比，中心显示总空间，点击扇区可联动过滤表空间 Top 10`,
    "chart-ts-bar": `${title}，横向条形，展示单表总空间与行数，支持与库占比联动`,
  };
  return map[id] || title;
}
