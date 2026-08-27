# -*- coding: utf-8 -*-
"""MySQL 服务管理：跨平台服务名探测 / 状态检测 / 重启(Windows net/sc、Linux systemctl、macOS brew)。

仅接管**本机** OS 级 MySQL 服务的启停/状态检测，数据库连接测试由调用方传入
(verify_cb)，判断"服务真的起来了"。远程库在本机没有对应服务，探测不到时返回 unknown。
"""
import re
import subprocess
import sys
import time

# 常见 MySQL 服务名（探测兜底顺序）
_KNOWN_NAMES = ["MySQL80", "MySQL", "MySQL57", "MySQL55", "MariaDB", "mysql", "mysqld", "mariadb"]


def _platform():
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _run(cmd, timeout=20):
    """执行命令，返回 (exit_code, stdout, stderr)；不可用/超时返回 None。"""
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
        # Windows 命令输出常为 GBK(本地代码页)，utf-8 解码会炸 reader 线程；errors=replace 容忍
        out = p.stdout.decode("utf-8", errors="replace")
        err = p.stderr.decode("utf-8", errors="replace")
        return p.returncode, out, err
    except Exception:
        return None


def _service_exists(name):
    """服务是否安装。不存在返回 False，探测失败返回 None。"""
    plat = _platform()
    try:
        if plat == "windows":
            r = _run(["sc", "query", name], timeout=15)
            if r is None:
                return None
            return r[0] == 0
        if plat == "macos":
            r = _run(["brew", "services", "info", name], timeout=15)
            return r is not None and r[0] == 0
        # linux: systemctl status 0=运行,3=已停止(存在),4=不存在
        r = _run(["systemctl", "status", name], timeout=15)
        if r is None:
            return None
        return r[0] in (0, 3)
    except Exception:
        return None


def _service_state(name):
    """返回 running / stopped / unknown / missing。"""
    plat = _platform()
    try:
        if plat == "windows":
            r = _run(["sc", "query", name], timeout=15)
            if r is None:
                return "unknown"
            if r[0] != 0:
                return "missing" if r[0] == 1060 else "unknown"
            m = re.search(r"STATE\s*:\s*(\d+)", r[1])
            if not m:
                return "unknown"
            return "running" if int(m.group(1)) == 4 else "stopped"
        if plat == "macos":
            r = _run(["brew", "services", "info", name], timeout=15)
            if r is None:
                return "unknown"
            out = (r[1] + r[2]).lower()
            if "started" in out:
                return "running"
            return "stopped" if "stopped" in out else "unknown"
        # linux
        r = _run(["systemctl", "is-active", name], timeout=15)
        if r is None:
            return "unknown"
        out = (r[1] or "").strip()
        if out == "active":
            return "running"
        if out in ("inactive", "failed"):
            return "stopped"
        return "missing" if r[0] == 4 else "unknown"
    except Exception:
        return "unknown"


def detect_service_name():
    """探测本机 MySQL 服务名。先枚举，再对已知名做存在性兜底。返回服务名或 None。"""
    plat = _platform()
    found = set()
    try:
        if plat == "windows":
            r = _run(["sc", "query", "type=", "service", "state=", "all"], timeout=25)
            if r:
                for line in (r[1] or "").splitlines():
                    m = re.match(r"SERVICE_NAME:\s*(\S+)", line.strip())
                    if m and re.search(r"mysql|maria", m.group(1), re.I):
                        found.add(m.group(1))
        elif plat == "macos":
            r = _run(["brew", "services", "list"], timeout=25)
            if r:
                for line in (r[1] or "").splitlines():
                    m = re.match(r"^(\S+)", line)
                    if m and re.search(r"mysql|maria", m.group(1), re.I):
                        found.add(m.group(1))
        else:
            r = _run(["systemctl", "list-unit-files", "--type=service"], timeout=25)
            if r:
                for line in (r[1] or "").splitlines():
                    m = re.match(r"^(\S+?)(?:\.service)?\s", line)
                    if m and re.search(r"mysql|maria", m.group(1), re.I):
                        found.add(m.group(1).removesuffix(".service"))
    except Exception:
        pass
    if found:
        return sorted(found)[0]
    # 兜底：已知常见名，挑第一个确实安装的
    for nm in _KNOWN_NAMES:
        if _service_exists(nm) is True:
            return nm
    return None


def service_status(name=None):
    """返回 {service_name, os_status, message}。name 为空时自动探测。"""
    name = name or detect_service_name()
    if not name:
        return {
            "service_name": None,
            "os_status": "unknown",
            "message": "未检测到本机 MySQL 服务（可能是远程数据库或以其它方式运行）",
        }
    st = _service_state(name)
    txt = {"running": "运行中", "stopped": "已停止",
           "missing": f"服务 {name} 不存在", "unknown": "状态未知"}[st]
    return {"service_name": name, "os_status": st, "message": txt}


def restart_service(name, verify_cb=None, verify_timeout=90):
    """重启服务并按 verify_cb 验证就绪。verify_cb 可选回调返回 True=可连。

    返回 {ok, msg, running, elapsed}。Windows 需管理员权限；失败返回可读原因。
    """
    plat = _platform()
    try:
        if plat == "windows":
            _run(["net", "stop", name], timeout=30)  # 已在停止则报错可忽略
            time.sleep(2)
            r = _run(["net", "start", name], timeout=30)
            if r is None or r[0] != 0:
                err = (r[2] if r else "命令执行失败").strip()
                return {"ok": False, "msg": f"启动服务失败: {err or '请确认已管理员身份运行'}", "running": False}
        elif plat == "macos":
            r = _run(["brew", "services", "restart", name], timeout=90)
            if r is None or r[0] != 0:
                return {"ok": False, "msg": "brew services restart 失败", "running": False}
        else:
            r = _run(["systemctl", "restart", name], timeout=90)
            if r is None or r[0] != 0:
                err = (r[2] if r else "systemctl 不可用").strip()
                return {"ok": False, "msg": f"重启服务失败: {err or 'systemctl 不可用'}", "running": False}
    except Exception as e:
        return {"ok": False, "msg": f"重启异常: {e}", "running": False}

    start = time.time()
    last = "unknown"
    while time.time() - start < verify_timeout:
        last = _service_state(name)
        if last == "missing":
            return {"ok": False, "msg": f"服务 {name} 不存在", "running": False}
        if last == "running" and (verify_cb is None or verify_cb()):
            return {"ok": True, "msg": "重启成功，服务已运行并可连接",
                    "running": True, "elapsed": round(time.time() - start, 1)}
        time.sleep(2)
    return {"ok": False, "msg": f"等待服务就绪超时（当前状态:{last}）",
            "running": last == "running", "elapsed": round(time.time() - start, 1)}