# -*- coding: utf-8 -*-
"""本机系统资源采集（纯标准库优先，psutil 可选增强）。

设计约束（项目约定：避免第三方硬依赖）：
- CPU/内存/磁盘空间：全平台纯标准库实现
  - Windows: ctypes 调 GetSystemTimes / GlobalMemoryStatusEx / GetDiskFreeSpaceEx
  - Linux/macOS: /proc/stat / /proc/meminfo / os.statvfs
- 磁盘 IOPS / 网络吞吐：psutil 可用时提供，缺失时返回 None（前端隐藏对应图表）
- CPU 使用率需要两次采样差值，模块级缓存上次快照；首次调用返回 None
"""
import os
import sys
import time

IS_WIN = sys.platform == "win32"

# psutil 可选
try:
    import psutil  # noqa
    _HAS_PSUTIL = True
except ImportError:
    psutil = None
    _HAS_PSUTIL = False

# ---------------- 内部：Windows ctypes 快照 ----------------
if IS_WIN:
    import ctypes
    from ctypes import wintypes

    _K32 = ctypes.windll.kernel32

    class _FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

    class _MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", wintypes.DWORD),
            ("dwMemoryLoad", wintypes.DWORD),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    def _win_cpu_times():
        idle = _FILETIME(); kern = _FILETIME(); user = _FILETIME()
        _K32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user))
        def _to_sec(ft):
            return (ft.dwHighDateTime << 32 | ft.dwLowDateTime) / 1e7
        # 内核时间含空闲，用户时间不含
        idle_s, kern_s, user_s = _to_sec(idle), _to_sec(kern), _to_sec(user)
        total_s = kern_s + user_s
        return idle_s, total_s

    def _win_mem():
        m = _MEMORYSTATUSEX(); m.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        if not _K32.GlobalMemoryStatusEx(ctypes.byref(m)):
            return None
        return m.ullTotalPhys, m.ullAvailPhys

    def _win_disk_usage(path):
        free = ctypes.c_ulonglong(); total = ctypes.c_ulonglong()
        if not _K32.GetDiskFreeSpaceExW(path, None, ctypes.byref(total), ctypes.byref(free)):
            return None
        return total.value, free.value
else:
    _win_cpu_times = _win_mem = _win_disk_usage = None

# ---------------- 内部：Linux /proc 快照 ----------------
def _linux_cpu_times():
    try:
        with open("/proc/stat", "r") as f:
            parts = f.readline().split()
        if not parts or parts[0] != "cpu":
            return None
        nums = [int(x) for x in parts[1:]]
        idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
        return idle, sum(nums)
    except Exception:
        return None


def _linux_mem():
    try:
        vals = {}
        with open("/proc/meminfo", "r") as f:
            for line in f:
                k, _, v = line.partition(":")
                vals[k] = int(v.strip().split()[0]) * 1024
        total = vals.get("MemTotal", 0)
        avail = vals.get("MemAvailable", vals.get("MemFree", 0))
        return total, avail
    except Exception:
        return None


def _posix_disk_usage(path):
    try:
        st = os.statvfs(path)
        return st.f_frsize * st.f_blocks, st.f_frsize * st.f_bavail
    except Exception:
        return None


# ---------------- 对外：采集快照 ----------------
_cpu_cache = {"idle": None, "total": None, "ts": None}


def _cpu_percent():
    """两次采样差值计算 CPU 使用率(%)，首次调用返回 None。"""
    now = time.time()
    if _HAS_PSUTIL:
        return psutil.cpu_percent(interval=None)
    if IS_WIN:
        snap = _win_cpu_times()
    else:
        snap = _linux_cpu_times()
    if not snap:
        return None
    idle, total = snap
    prev = _cpu_cache
    if prev["total"] is None or prev["ts"] is None or now - prev["ts"] > 10:
        _cpu_cache.update(idle=idle, total=total, ts=now)
        return None
    d_total = total - prev["total"]
    d_idle = idle - prev["idle"]
    _cpu_cache.update(idle=idle, total=total, ts=now)
    if d_total <= 0:
        return None
    return round(max(0.0, min(100.0, (1 - d_idle / d_total) * 100)), 1)


def _mem_percent():
    """内存使用率(%)。"""
    if _HAS_PSUTIL:
        return round(psutil.virtual_memory().percent, 1)
    if IS_WIN:
        snap = _win_mem()
    else:
        snap = _linux_mem()
    if not snap:
        return None
    total, avail = snap
    if total <= 0:
        return None
    return round(max(0.0, min(100.0, (total - avail) / total * 100)), 1)


def _disk_percent(path):
    """磁盘空间使用率(%)，path 为目录或盘符。"""
    if not path:
        return None
    if _HAS_PSUTIL:
        try:
            return round(psutil.disk_usage(path).percent, 1)
        except Exception:
            return None
    if IS_WIN:
        snap = _win_disk_usage(path)
    else:
        snap = _posix_disk_usage(path)
    if not snap:
        return None
    total, free = snap
    if total <= 0:
        return None
    return round((total - free) / total * 100, 1)


def _io_metrics():
    """磁盘 IOPS/吞吐 与 网络吞吐(KB/s)，一次 1s 采样同时取两者。"""
    if not _HAS_PSUTIL:
        return None
    try:
        d1 = psutil.disk_io_counters()
        n1 = psutil.net_io_counters()
        time.sleep(1)
        d2 = psutil.disk_io_counters()
        n2 = psutil.net_io_counters()
        out = {}
        if d1 and d2:
            iops = (d2.read_count - d1.read_count) + (d2.write_count - d1.write_count)
            kb = ((d2.read_bytes - d1.read_bytes) + (d2.write_bytes - d1.write_bytes)) / 1024
            out["disk_io"] = {"iops": max(iops, 0), "kbs": round(max(kb, 0), 1)}
        if n1 and n2:
            nkb = ((n2.bytes_recv - n1.bytes_recv) + (n2.bytes_sent - n1.bytes_sent)) / 1024
            out["net_kbs"] = round(max(nkb, 0), 1)
        return out or None
    except Exception:
        return None


def sys_resources(disk_path=""):
    """汇总本机系统资源。disk_path 为空时自动选择：
    Windows 取 C:\；Linux 取 /。"""
    if not disk_path:
        disk_path = "C:\\" if IS_WIN else "/"
    io = _io_metrics() or {}
    return {
        "cpu_percent": _cpu_percent(),
        "mem_percent": _mem_percent(),
        "disk_percent": _disk_percent(disk_path),
        "disk_path": disk_path,
        "disk_io": io.get("disk_io"),
        "net_kbs": io.get("net_kbs"),
        "has_psutil": _HAS_PSUTIL,
        "ts": time.time(),
    }


if __name__ == "__main__":
    # 自测：python sys_resources.py
    import json
    print(json.dumps(sys_resources(), ensure_ascii=False, indent=2))
    print("psutil:", "可用" if _HAS_PSUTIL else "未安装(IOPS/网络吞吐将隐藏)")
