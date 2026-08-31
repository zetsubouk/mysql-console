# -*- coding: utf-8 -*-
"""告警 / 健康评分历史采样与分析上下文(轻量落盘, 2026-08-31 从 server.py 拆出)。

这些函数只依赖标准库 + paths.DATA_DIR,不触碰 HTTP/Handler,可独立单测。
- 告警历史:每 60s 采样一次,写 data/alerts_history.json;近 24h 分钟级,更早按小时聚合。
- 健康评分:近 24h 分钟级,更早 10 分钟级(取均值),写 data/health_history.json。
- ai_report_context:汇总两类采样为 AI 报告用的纯文本上下文。
"""
import json
import os
import threading
import time

import paths

# ---------------- 告警历史采样（轻量落盘） ----------------
_ALERT_FILE = os.path.join(paths.DATA_DIR, "alerts_history.json")
_ALERT_KEEP = 7 * 86400          # 保留 7 天
_ALERT_MINUTE_KEEP = 86400       # 近 24h 保留分钟级
_ALERT_LOCK = threading.Lock()


def alert_level_count(alerts):
    c = {"warning": 0, "critical": 0}
    for a in alerts or []:
        lv = "critical" if a.get("level") == "critical" else "warning"
        c[lv] = c.get(lv, 0) + 1
    return c


def _load_alert_history():
    try:
        with open(_ALERT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data.get("points"), list):
            return data
    except Exception:
        pass
    return {"points": [], "updated_at": ""}


def _save_alert_history(data):
    tmp = _ALERT_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, _ALERT_FILE)


def _rollup_alert_points(pts, now):
    """近 24h 保留分钟级,更早聚合为小时级,控制文件体积。"""
    minute_cut = now - _ALERT_MINUTE_KEEP
    recent = [p for p in pts if p["t"] >= minute_cut]
    older = [p for p in pts if p["t"] < minute_cut]
    hour_map = {}
    for p in older:
        h = (p["t"] // 3600) * 3600
        b = hour_map.setdefault(h, {"t": h, "warning": 0, "critical": 0})
        b["warning"] += p.get("warning", 0)
        b["critical"] += p.get("critical", 0)
    return recent + sorted(hour_map.values(), key=lambda x: x["t"])


def append_alert_sample(alerts):
    now = time.time()
    t = int(now // 60) * 60
    counts = alert_level_count(alerts)
    with _ALERT_LOCK:
        data = _load_alert_history()
        pts = data.get("points") or []
        if pts and pts[-1]["t"] == t:
            pts[-1].update(counts)
        else:
            pts.append({"t": t, **counts})
        cutoff = now - _ALERT_KEEP
        pts = [p for p in pts if p["t"] >= cutoff]
        if len(pts) > 1600:
            pts = _rollup_alert_points(pts, now)
        data["points"] = pts
        data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _save_alert_history(data)


def alert_history_query(path):
    days = 7
    try:
        if "?" in path:
            import urllib.parse
            qs = urllib.parse.parse_qs(path.split("?", 1)[1])
            if qs.get("days"):
                days = max(1, min(7, int(qs["days"][0])))
    except Exception:
        days = 7
    data = _load_alert_history()
    pts = data.get("points") or []
    cutoff = time.time() - days * 86400
    pts = [p for p in pts if p["t"] >= cutoff]
    return {
        "days": days,
        "points": pts,
        "updated_at": data.get("updated_at", ""),
        "levels": ["warning", "critical"],
    }


# ---------------- 健康评分历史采样 (A1) ----------------
_HEALTH_FILE = os.path.join(paths.DATA_DIR, "health_history.json")
_HEALTH_KEEP = 7 * 86400          # 保留 7 天
_HEALTH_MINUTE_KEEP = 86400       # 近 24h 保留分钟级
_HEALTH_LOCK = threading.Lock()


def _load_health_history():
    try:
        with open(_HEALTH_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data.get("points"), list):
            return data
    except Exception:
        pass
    return {"points": [], "updated_at": ""}


def _save_health_history(data):
    tmp = _HEALTH_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, _HEALTH_FILE)


def _rollup_health_points(pts, now):
    """近 24h 保留分钟级,更早聚合为 10 分钟级(取均值),控制文件体积。"""
    minute_cut = now - _HEALTH_MINUTE_KEEP
    recent = [p for p in pts if p["t"] >= minute_cut]
    older = [p for p in pts if p["t"] < minute_cut]
    bucket = {}
    for p in older:
        h = (p["t"] // 600) * 600
        b = bucket.setdefault(h, {"t": h, "sum": 0.0, "n": 0})
        b["sum"] += p.get("score", 0)
        b["n"] += 1
    merged = []
    for h in sorted(bucket):
        b = bucket[h]
        merged.append({"t": h, "score": round(b["sum"] / b["n"], 1)})
    return recent + merged


def append_health_sample(score):
    if score is None:
        return
    now = time.time()
    t = int(now // 60) * 60
    with _HEALTH_LOCK:
        data = _load_health_history()
        pts = data.get("points") or []
        if pts and pts[-1]["t"] == t:
            pts[-1]["score"] = score
        else:
            pts.append({"t": t, "score": score})
        cutoff = now - _HEALTH_KEEP
        pts = [p for p in pts if p["t"] >= cutoff]
        if len(pts) > 1600:
            pts = _rollup_health_points(pts, now)
        data["points"] = pts
        data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _save_health_history(data)


def health_history_query(path):
    hours = 24
    try:
        if "?" in path:
            import urllib.parse
            qs = urllib.parse.parse_qs(path.split("?", 1)[1])
            if qs.get("hours"):
                hours = max(1, min(168, int(qs["hours"][0])))
    except Exception:
        hours = 24
    data = _load_health_history()
    pts = data.get("points") or []
    cutoff = time.time() - hours * 3600
    pts = [p for p in pts if p["t"] >= cutoff]
    return {
        "hours": hours,
        "points": pts,
        "updated_at": data.get("updated_at", ""),
    }


def ai_report_context(rtype):
    """汇总告警/健康采样数据为 AI 报告上下文(纯文本)。"""
    import datetime
    if rtype == "alert":
        data = _load_alert_history()
        pts = data.get("points") or []
        cutoff = time.time() - 7 * 86400
        pts = [p for p in pts if p["t"] >= cutoff]
        total_w = sum(p.get("warning", 0) for p in pts)
        total_c = sum(p.get("critical", 0) for p in pts)
        latest = pts[-1] if pts else {}
        samples = []
        for p in pts:
            ts = datetime.datetime.fromtimestamp(p["t"]).strftime("%m-%d %H:%M")
            samples.append(f"{ts}: warning={p.get('warning',0)} critical={p.get('critical',0)}")
        parts = [
            f"近 7 天告警采样点 {len(pts)} 个。",
            f"累计告警总量: warning {total_w}, critical {total_c}。",
            f"最新采样: warning={latest.get('warning',0)} critical={latest.get('critical',0)}。",
            "采样明细(时间: warning = 计数, critical = 计数):",
        ]
        parts += samples[-120:]  # 控制长度,只给最近 120 个点
        return "\n".join(parts)
    # 健康报告
    data = _load_health_history()
    pts = data.get("points") or []
    cutoff = time.time() - 7 * 86400
    pts = [p for p in pts if p["t"] >= cutoff]
    if not pts:
        return "近 7 天无健康评分采样数据(可能仅为工具未运行或连接未激活)。"
    scores = [p.get("score", 0) for p in pts]
    samples = []
    for p in pts:
        ts = datetime.datetime.fromtimestamp(p["t"]).strftime("%m-%d %H:%M")
        samples.append(f"{ts}: {p.get('score',0)}")
    parts = [
        f"近 7 天健康评分采样点 {len(pts)} 个。",
        f"评分区间: {min(scores):.1f} ~ {max(scores):.1f},当前 {scores[-1]:.1f}。",
        "采样明细(时间: 评分):",
    ]
    parts += samples[-144:]
    return "\n".join(parts)