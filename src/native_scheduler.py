# -*- coding: utf-8 -*-
"""系统计划任务适配层:自动识别操作系统,注册/反注册/查询定时备份任务。

Windows  -> schtasks(计划任务)
Linux/macOS -> crontab（单仓库双目录后 Linux 仅 cron，废 systemd timer；mac 按 linux 模式）
其他     -> 不支持,前端隐藏该选项

注册时由 native_script 生成自包含备份脚本(Windows .ps1 / Linux .sh),
计划任务只调用脚本,不再经过 python 解释器:
  Windows -> powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File scripts\\backup_<id>.ps1
  Linux   -> /bin/bash scripts/backup_<id>.sh
(-WindowStyle Hidden:计划任务执行时不弹出控制台窗口)
任务名统一前缀 MySQLConsole_ 便于识别与清理。
"""
import os
import platform
import shutil
import subprocess
import sys

import config_store
import native_script

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PREFIX = "MySQLConsole_"
# 生成的备份脚本统一存放于项目 scripts/ 目录(与 install.bat 等平级)
SCRIPTS_DIR = os.path.normpath(os.path.join(BASE_DIR, "..", "scripts"))

OS_TYPE = "windows" if sys.platform == "win32" else "linux"

# Linux/macOS 仅 cron（废 systemd；mac 按 linux 模式）
LINUX_SCHED = "cron" if OS_TYPE == "linux" else None

NATIVE_LABELS = {
    ("windows", None): "Windows 计划任务",
    ("linux", "cron"): "crontab",
}


def env_info():
    """GET /api/schedules/env 返回环境信息,前端据此渲染调度选项。"""
    native_name = NATIVE_LABELS.get((OS_TYPE, LINUX_SCHED))
    return {
        "os": OS_TYPE,
        "os_desc": f"{platform.system()} {platform.release()}",
        "native_engine": native_name,
        "native_available": bool(native_name),
        "python_path": sys.executable,
        "cli_path": os.path.join(BASE_DIR, "cli_backup.py"),
    }


def _run(cmd, timeout=30):
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
        out = (p.stdout or b"").decode("utf-8", "replace") + \
              (p.stderr or b"").decode("utf-8", "replace")
        return p.returncode == 0, out.strip()
    except Exception as e:
        return False, str(e)


# ---------------- 备份脚本生成 ----------------

def _task_context(task):
    """取任务生成脚本所需的连接配置与全局设置(与 cli_backup.py 同源)。"""
    conn = config_store.get_connection(task.get("conn_id")) if task.get("conn_id") else None
    if not conn:
        raise RuntimeError(f"任务连接不可用: {task.get('conn_id')}")
    return conn, config_store.get_settings()


def _script_path(task):
    ext = ".ps1" if OS_TYPE == "windows" else ".sh"
    return os.path.join(SCRIPTS_DIR, "backup_%s%s" % (task["id"], ext))


def _generate_script(task):
    """生成当前平台备份脚本,返回绝对路径。失败时抛异常。"""
    conn, settings = _task_context(task)
    return native_script.build(task, conn, settings, SCRIPTS_DIR, OS_TYPE)


def _remove_script(task):
    try:
        os.remove(_script_path(task))
    except OSError:
        pass


# ---------------- Windows schtasks ----------------

def _win_sch_args(task):
    """按任务周期生成 schtasks 周期参数。"""
    freq = task.get("freq")
    tm = task.get("time", "02:00")
    if freq == "hourly":
        n = max(1, int(task.get("interval_hours", 1)))
        return ["/sc", "hourly", "/mo", str(n)]
    if freq == "daily":
        return ["/sc", "daily", "/st", tm]
    if freq == "weekly":
        days = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]
        return ["/sc", "weekly", "/d", days[int(task.get("weekday", 0))], "/st", tm]
    if freq == "monthly":
        return ["/sc", "monthly", "/d", str(task.get("day_of_month", 1)), "/st", tm]
    if freq == "once":
        at = (task.get("at_once") or "").replace("T", " ")
        date, hhmm = at.split(" ")[0], "02:00"
        if " " in at:
            date, hhmm = at.rsplit(" ", 1)
        return ["/sc", "once", "/sd", date.replace("-", "/"), "/st", hhmm[:5]]
    raise ValueError(f"不支持的周期: {freq}")


def _register_windows(task):
    name = PREFIX + task["id"]
    try:
        script = _generate_script(task)
    except Exception as e:
        return {"ok": False, "error": f"生成备份脚本失败: {e}"}
    cmd = ["schtasks", "/create", "/f", "/tn", name,
           "/tr", f'powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{script}"']
    cmd += _win_sch_args(task)
    ok, out = _run(cmd)
    if not ok:
        return {"ok": False, "error": f"schtasks 注册失败: {out}",
                "command": " ".join(f'"{c}"' if " " in c else c for c in cmd)}
    return {"ok": True, "native_name": name}


def _unregister_windows(task):
    name = PREFIX + task["id"]
    ok, out = _run(["schtasks", "/delete", "/tn", name, "/f"])
    # 任务不存在也算成功(幂等)
    if not ok and "does not exist" not in out and "不存在" not in out:
        return {"ok": False, "error": f"schtasks 删除失败: {out}"}
    _remove_script(task)
    return {"ok": True}


def _status_windows(task):
    ok, out = _run(["schtasks", "/query", "/tn", PREFIX + task["id"]])
    return {"ok": True, "registered": ok, "detail": out[:300]}


# ---------------- Linux ----------------

def _cli_cmd(task):
    """返回系统计划任务实际执行的命令(Linux): /bin/bash <备份脚本>。"""
    return f"/bin/bash {_script_path(task)}"


def _oncalendar(task):
    """把任务周期转为 systemd OnCalendar 表达式（已废弃，仅保留兼容）。"""
    raise NotImplementedError("systemd timer 已废弃，Linux 仅 cron")


def _register_linux(task):
    tid = task["id"]
    try:
        _generate_script(task)
    except Exception as e:
        return {"ok": False, "error": f"生成备份脚本失败: {e}"}
    # Linux 仅 cron（systemd 已废）
    line = _cron_line(task)
    marker = f"#mysqlconsole:{tid}"
    entry = f"{line} {_cli_cmd(task)} {marker}"
    ok, cur = _run(["crontab", "-l"])
    lines = [l for l in (cur.splitlines() if ok else []) if marker not in l]
    lines.append(entry)
    p = subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n",
                       capture_output=True)
    if p.returncode != 0:
        return {"ok": False, "error": "crontab 写入失败",
                "command": entry}
    return {"ok": True, "native_name": marker}


def _cron_line(task):
    """把任务周期转为 crontab 五段表达式。"""
    freq = task.get("freq")
    h, m = (task.get("time", "02:00").split(":") + ["0"])[:2]
    if freq == "hourly":
        n = int(task.get("interval_hours", 1))
        return f"{m} */{n} * * *"
    if freq == "daily":
        return f"{m} {h} * * *"
    if freq == "weekly":
        return f"{m} {h} * * {int(task.get('weekday', 0))}"
    if freq == "monthly":
        return f"{m} {h} {int(task.get('day_of_month', 1))} * *"
    if freq == "once":
        at = (task.get("at_once") or "").replace("T", " ").split(" ")
        d = at[0].split("-") if at else ["*", "*", "*"]
        hm = (at[1].split(":") + ["0"])[:2] if len(at) > 1 else ["2", "0"]
        return f"{hm[1]} {hm[0]} {int(d[2])} {int(d[1])} *"
    return "0 2 * * *"


def _unregister_linux(task):
    tid = task["id"]
    marker = f"#mysqlconsole:{tid}"
    ok, cur = _run(["crontab", "-l"])
    if not ok:
        return {"ok": True}
    lines = [l for l in cur.splitlines() if marker not in l]
    subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", capture_output=True)
    _remove_script(task)
    return {"ok": True}


def _status_linux(task):
    tid = task["id"]
    marker = f"#mysqlconsole:{tid}"
    ok, cur = _run(["crontab", "-l"])
    return {"ok": True, "registered": ok and marker in cur, "detail": ""}


_DISPATCH = {
    ("windows", None): (_register_windows, _unregister_windows, _status_windows),
    ("linux", None): (_register_linux, _unregister_linux, _status_linux),
}


def register(task):
    fn = _DISPATCH.get((OS_TYPE, None))
    if not fn:
        return {"ok": False, "error": f"{OS_TYPE} 暂不支持系统计划任务"}
    r = fn[0](task)
    return r


def unregister(task):
    fn = _DISPATCH.get((OS_TYPE, None))
    if not fn:
        return {"ok": False, "error": f"{OS_TYPE} 暂不支持系统计划任务"}
    return fn[1](task)


def status(task):
    fn = _DISPATCH.get((OS_TYPE, None))
    if not fn:
        return {"ok": False, "registered": False, "detail": "不支持"}
    return fn[2](task)


def gen_command(task):
    """生成注册命令行文本(注册失败时给用户手动执行的兜底)。"""
    if OS_TYPE == "windows":
        cmd = ["schtasks", "/create", "/f", "/tn", PREFIX + task["id"],
               "/tr", f'powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{_script_path(task)}"']
        cmd += _win_sch_args(task)
        return " ".join(f'"{c}"' if " " in c else c for c in cmd)
    # Linux 仅 cron
    return f"{_cron_line(task)} {_cli_cmd(task)}"
