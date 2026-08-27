# -*- coding: utf-8 -*-
"""定时备份任务存储:多任务模型 + 旧单任务配置自动迁移。

数据文件: data/schedule_tasks.json
任务字段:
  id             任务 id(12 位 hex)
  name           任务名称
  enabled        是否启用
  engine         builtin=内置调度器 | native=系统计划任务
  freq           hourly | daily | weekly | monthly | once
  interval_hours hourly 用: 每 N 小时(1-23)
  weekday        weekly 用: 0=周日 ... 6=周六
  day_of_month   monthly 用: 1-31
  time           daily/weekly/monthly 用: "HH:MM"
  at_once        once 用: "YYYY-MM-DDTHH:MM"
  dbs            备份库列表(空=全库)
  keep           保留最近 N 份
  backup_dir     备份目录(空=全局默认)
  conn_id        绑定连接 id
  last_run       上次执行时间
  last_result    上次执行结果 success|failed|""
"""
import json
import os
import threading
import time
import uuid

import config_store
import local_store
from config_store import _is_full_mode, _get_backend

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
TASKS_PATH = os.path.join(DATA_DIR, "schedule_tasks.json")

FREQ_LABELS = {
    "hourly": "每小时", "daily": "每天", "weekly": "每周",
    "monthly": "每月", "once": "仅一次",
}
WEEKDAY_LABELS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"]

_lock = threading.Lock()


def _default_task():
    return {
        "id": "", "name": "", "enabled": False, "engine": "builtin",
        "freq": "daily", "interval_hours": 1, "weekday": 0, "day_of_month": 1,
        "time": "02:00", "at_once": "",
        "dbs": [], "keep": 7, "backup_dir": "", "conn_id": "",
        "last_run": "", "last_result": "",
    }


def _load():
    # 轻量模式数据统一存 SQLite(config.db meta)。旧 schedule_tasks.json 一次性迁移。
    if os.path.exists(TASKS_PATH):
        try:
            with open(TASKS_PATH, encoding="utf-8") as f:
                tasks = json.load(f)
            if local_store.get_meta_json("schedules") is None:
                local_store.set_meta_json("schedules", tasks)
            try:
                os.remove(TASKS_PATH)
            except OSError:
                pass
        except Exception:
            pass
    return local_store.get_meta_json("schedules")


def _save(tasks):
    local_store.set_meta_json("schedules", tasks)


def _migrate_legacy():
    """旧版 config.json 的 schedule_* 单任务配置迁移为一个 builtin daily 任务。"""
    tasks = []
    s = config_store.get_settings()
    cron = (s.get("schedule_cron") or "0 2 * * *").split()
    time_str = "02:00"
    if len(cron) == 5 and cron[1].isdigit() and cron[0].isdigit():
        time_str = f"{int(cron[1]):02d}:{int(cron[0]):02d}"
    t = _default_task()
    t.update({
        "id": uuid.uuid4().hex[:12],
        "name": "默认定时备份(迁移自旧配置)",
        "enabled": bool(s.get("schedule_enabled")),
        "freq": "daily",
        "time": time_str,
        "dbs": s.get("schedule_dbs") or [],
        "keep": int(s.get("schedule_keep", 7)),
        "conn_id": s.get("schedule_conn_id") or "",
    })
    tasks.append(t)
    _save(tasks)
    # 关闭旧调度开关,避免双跑
    config_store.save_settings({"schedule_enabled": False})
    return tasks


def list_tasks():
    if _is_full_mode():
        try:
            backend = _get_backend()
            return backend.list_schedules()
        except Exception:
            return []
    with _lock:
        tasks = _load()
        if tasks is None:
            tasks = _migrate_legacy()
        return [dict(t) for t in tasks]


def get_task(tid):
    for t in list_tasks():
        if t["id"] == tid:
            return t
    return None


def save_task(payload, tid=None):
    """新建或更新任务。校验字段,返回任务 id。"""
    if _is_full_mode():
        backend = _get_backend()
        # 转换字段格式适配 mc_schedule 表
        task_data = dict(payload)
        if "engine" in task_data and "schedule_type" not in task_data:
            task_data["schedule_type"] = task_data.pop("engine")
        if "freq" in task_data and "cron_expr" not in task_data:
            # 转换 freq -> cron_expr
            freq = task_data.get("freq", "daily")
            time_str = task_data.get("time", "02:00")
            h, m = time_str.split(":")
            if freq == "hourly":
                task_data["cron_expr"] = f"0 */{task_data.get('interval_hours', 1)} * * *"
            elif freq == "daily":
                task_data["cron_expr"] = f"{m} {h} * * *"
            elif freq == "weekly":
                wd = task_data.get("weekday", 0)
                task_data["cron_expr"] = f"{m} {h} * * {wd}"
            elif freq == "monthly":
                task_data["cron_expr"] = f"{m} {h} {task_data.get('day_of_month', 1)} * *"
            else:
                task_data["cron_expr"] = "0 2 * * *"
        # 处理 dbs 列表
        if "dbs" in task_data and isinstance(task_data["dbs"], list):
            import json
            task_data["dbs"] = json.dumps(task_data["dbs"], ensure_ascii=False)
        return backend.save_schedule(task_data, tid)
    t = _default_task()
    if tid:
        exist = get_task(tid)
        if not exist:
            raise ValueError("任务不存在")
        t.update(exist)
    for k in ("name", "engine", "freq", "time", "at_once",
              "backup_dir", "conn_id", "last_run", "last_result"):
        if k in payload:
            t[k] = str(payload[k] or "").strip()
    for k in ("enabled",):
        if k in payload:
            t[k] = bool(payload[k])
    freq = payload.get("freq", t["freq"])
    if freq not in FREQ_LABELS:
        raise ValueError(f"不支持的周期类型: {freq}")
    t["freq"] = freq
    try:
        if "interval_hours" in payload:
            t["interval_hours"] = max(1, min(23, int(payload["interval_hours"])))
        if "weekday" in payload:
            t["weekday"] = max(0, min(6, int(payload["weekday"])))
        if "day_of_month" in payload:
            t["day_of_month"] = max(1, min(31, int(payload["day_of_month"])))
        if "keep" in payload:
            t["keep"] = max(1, min(99, int(payload["keep"])))
    except (TypeError, ValueError):
        raise ValueError("数字字段格式错误")
    dbs = payload.get("dbs", t["dbs"])
    if isinstance(dbs, list):
        t["dbs"] = [str(d) for d in dbs]
    if not t["name"]:
        raise ValueError("请填写任务名称")
    if t["freq"] in ("daily", "weekly", "monthly"):
        parts = t["time"].split(":")
        if len(parts) != 2 or not all(p.isdigit() for p in parts) \
                or not (0 <= int(parts[0]) <= 23 and 0 <= int(parts[1]) <= 59):
            raise ValueError("时间格式应为 HH:MM")
        t["time"] = f"{int(parts[0]):02d}:{int(parts[1]):02d}"
    if t["freq"] == "once" and not t["at_once"]:
        raise ValueError("请选择一次性执行的时间")
    if not t["conn_id"]:
        cs = config_store.list_connections()
        t["conn_id"] = cs[0]["id"] if cs else ""
    if not tid:
        t["id"] = uuid.uuid4().hex[:12]
    with _lock:
        tasks = _load() or []
        if tid:
            tasks = [t if x.get("id") == tid else x for x in tasks]
        else:
            tasks.append(t)
        _save(tasks)
    return t["id"]


def delete_task(tid):
    if _is_full_mode():
        try:
            backend = _get_backend()
            backend.delete_schedule(tid)
            return True
        except Exception:
            return False
    with _lock:
        tasks = _load() or []
        remain = [t for t in tasks if t.get("id") != tid]
        if len(remain) == len(tasks):
            return False
        _save(remain)
        return True


def set_enabled(tid, enabled):
    if _is_full_mode():
        try:
            backend = _get_backend()
            task = backend.get_schedule(tid)
            if task:
                task["enabled"] = enabled
                backend.save_schedule(task, tid)
                return True
            return False
        except Exception:
            return False
    with _lock:
        tasks = _load() or []
        hit = False
        for t in tasks:
            if t.get("id") == tid:
                t["enabled"] = bool(enabled)
                hit = True
        if hit:
            _save(tasks)
        return hit


def update_run_status(tid, result):
    """执行后回写 last_run / last_result。result: success|failed"""
    if _is_full_mode():
        try:
            backend = _get_backend()
            backend.update_schedule_status(tid, result)
        except Exception:
            pass
        return
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        tasks = _load() or []
        for t in tasks:
            if t.get("id") == tid:
                t["last_run"] = now
                t["last_result"] = result
        _save(tasks)


def set_native_registered(tid, registered):
    """记录任务是否已注册到系统计划任务。"""
    if _is_full_mode():
        try:
            backend = _get_backend()
            task = backend.get_schedule(tid)
            if task:
                task["native_registered"] = bool(registered)
                backend.save_schedule(task, tid)
                return True
            return False
        except Exception:
            return False
    with _lock:
        tasks = _load() or []
        hit = False
        for t in tasks:
            if t.get("id") == tid:
                t["native_registered"] = bool(registered)
                hit = True
        if hit:
            _save(tasks)
        return hit


# ---------------- 到点匹配 ----------------

def is_due(task, now=None, check_enabled=True):
    """判断任务在当前分钟是否应触发。now 为 struct_time。check_enabled=False 用于单测。"""
    if check_enabled and not task.get("enabled"):
        return False
    now = now or time.localtime()
    hhmm = f"{now.tm_hour:02d}:{now.tm_min:02d}"
    freq = task.get("freq")
    if freq == "hourly":
        return True  # 由调用方按 last_run 间隔控制
    if freq in ("daily", "weekly", "monthly"):
        if hhmm != task.get("time"):
            return False
        if freq == "weekly":
            # tm_wday: 0=周一...6=周日; 任务 weekday: 0=周日...6=周六
            return (now.tm_wday + 1) % 7 == int(task.get("weekday", 0))
        if freq == "monthly":
            return now.tm_mday == int(task.get("day_of_month", 1))
        return True
    if freq == "once":
        at = task.get("at_once", "")  # "YYYY-MM-DDTHH:MM"
        try:
            target = time.mktime(time.strptime(at, "%Y-%m-%dT%H:%M"))
            cur = time.mktime(time.strptime(
                time.strftime("%Y-%m-%d %H:%M", now), "%Y-%m-%d %H:%M"))
            return abs(cur - target) < 60
        except Exception:
            return False
    return False


def describe(task):
    """生成人性化周期描述,供前端展示。"""
    freq = task.get("freq")
    if freq == "hourly":
        n = task.get("interval_hours", 1)
        return f"每 {n} 小时" if n > 1 else "每小时"
    if freq in ("daily", "weekly", "monthly"):
        tm = task.get("time", "")
        if freq == "daily":
            return f"每天 {tm}"
        if freq == "weekly":
            return f"每周{WEEKDAY_LABELS[int(task.get('weekday', 0))]} {tm}".replace("每周周", "每周")
        return f"每月 {task.get('day_of_month', 1)} 日 {tm}"
    if freq == "once":
        return f"一次性: {(task.get('at_once') or '').replace('T', ' ')}"
    return FREQ_LABELS.get(freq, freq)
