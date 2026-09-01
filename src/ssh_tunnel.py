# -*- coding: utf-8 -*-
"""SSH 隧道(远程备份):3306 直连不通时,用系统 ssh 建立端口转发。

背景:目标 MySQL 部署在跳板机/内网之后,客户机(Windows/Linux)无法直连
3306。通过 SSH 本地端口转发把远程 MySQL 端口映射到本机 127.0.0.1:<local_port>,
随后 mysqldump / mysql / pymysql 都连这个本地端口即可。

认证:非交互场景下仅支持密钥(ssh -i)或默认 ~/.ssh 密钥;
密码登录需 tty/expect,不在 CLI 场景支持(引导明确提示改用密钥)。
"""
import os
import socket
import subprocess
import threading
import time

import sys

IS_WIN = sys.platform == "win32"

# SSH 隧道子进程(仅进行一次加密密钥清洗,见 ensure_tunnel_stopped)
_TUNNELS = {}
_TUNNELS_LOCK = threading.Lock()


def ssh_available():
    """系统是否存在 ssh 可执行文件。"""
    import shutil
    name = "ssh.exe" if IS_WIN else "ssh"
    return bool(shutil.which(name) or shutil.which("ssh"))


def pick_free_port(preferred=0):
    """选一个空闲本地端口;preferred<=0 时系统分配。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", preferred or 0))
        return s.getsockname()[1]


def is_ssh_cfg(cfg):
    """连接是否启用了 SSH 隧道。"""
    return bool(cfg and cfg.get("ssh_enabled") and (cfg.get("ssh_host") or "").strip())


def ssh_prefix(cfg):
    """构造 ssh 连接前缀参数(不含远端命令):[-i key] [-p port] [-o ...] user@host。

    供“远程备份直写文件/读取文件/取大小”复用。校验 ssh_host 与密钥文件存在。
    """
    ssh_host = (cfg.get("ssh_host") or "").strip()
    if not ssh_host:
        raise ValueError("未配置 SSH 主机(远程备份需要 SSH 宿主机)")
    port = int(cfg.get("ssh_port") or 22)
    user = (cfg.get("ssh_user") or "").strip() or (cfg.get("user") or "root")
    key = (cfg.get("ssh_key") or "").strip()
    pre = [
        "-T",
        "-p", str(port),
        "-o", "StrictHostKeyChecking=no",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
    ]
    if key:
        if not os.path.isfile(key):
            raise ValueError("SSH 私钥文件不存在: %s" % key)
        pre += ["-i", key]
    return ["ssh"] + pre + ["%s@%s" % (user, ssh_host)]


def remote_file_size(cfg, remote_cmd):
    """取远程文件字节数(由远端 shell 命令的 stdout 决定)。

    remote_cmd 是在远端执行的 shell 命令(例如 ``gzip -dc 'path' | wc -c`` 或
    ``wc -c < 'path'``),而非裸文件路径；传裸路径会导致 ssh 把路径当命令
    执行恒失败。历史签名 ``remote_file_size(cfg, remote_path)`` 已更名为
    ``remote_cmd`` 以消除误导，旧调用仍兼容(裸路径按 ``wc -c < path`` 执行)。
    失败返回 -1。
    """
    cmd = (remote_cmd or "").strip()
    if not cmd:
        return -1
    if "/" in cmd and not any(tok in cmd for tok in ("|", ";", "`", "$", "<", ">", "'", '"')):
        import shlex
        cmd = "wc -c < %s" % shlex.quote(cmd)
    try:
        pre = ssh_prefix(cfg)
        proc = subprocess.run(pre + [cmd],
                              capture_output=True, timeout=30)
        out = (proc.stdout or b"").decode("utf-8", "replace").strip()
        return int(out) if out.lstrip("-").isdigit() else -1
    except Exception:
        return -1


def remote_file_size_by_path(cfg, remote_path):
    """按裸路径取远程文件大小的便捷包装(等价于 ``wc -c < path``)。"""
    import shlex
    return remote_file_size(cfg, "wc -c < %s" % shlex.quote(remote_path))


def read_remote_stream(cfg, remote_cmd):
    """打开远端只读流(gzip -dc path | wc 等),返回 Popen(stdout=PIPE) 或 None。"""
    try:
        pre = ssh_prefix(cfg)
    except ValueError:
        return None
    try:
        return subprocess.Popen(pre + [remote_cmd], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                creationflags=subprocess.CREATE_NO_WINDOW if IS_WIN else 0,
                                start_new_session=(not IS_WIN))
    except OSError:
        return None


def ssh_run(cfg, cmd, timeout=30):
    """在远端执行一条命令,返回 stdout(去首尾空白);失败返回空串。只读探测用。"""
    try:
        pre = ssh_prefix(cfg)
    except ValueError:
        return ""
    try:
        proc = subprocess.run(pre + [cmd], capture_output=True, timeout=timeout,
                              creationflags=subprocess.CREATE_NO_WINDOW if IS_WIN else 0,
                              start_new_session=(not IS_WIN))
        return (proc.stdout or b"").decode("utf-8", "replace").strip()
    except Exception:
        return ""


def probe_remote_env(cfg):
    """探测远程服务器的操作系统与备份 shell 环境(只读,不改远端)。

    返回 dict:
      os:      'linux' / 'windows' / 'unknown'
      git_bash: 是否运行在 Git Bash(Windows 远程备份就绪判定)
      detail:  原始探测输出(排障用)

    探测策略:
      1) uname -s: Linux 输出 "Linux";Git Bash 环境输出 MINGW*/MSYS*/CYGWIN*(判 Windows);
      2) 无输出(Windows OpenSSH 默认 cmd/PowerShell shell 无 uname)再试 ver/cmd ver/pwsh。
         PowerShell 7 + OpenSSH 场景下 uname 缺失，旧逻辑判 unknown，现通过 ver + PSVersion 精确判 Windows。
    """
    out = ""
    try:
        out = ssh_run(cfg, "uname -s")
    except Exception:
        out = ""
    low = (out or "").lower()
    if "linux" in low:
        return {"os": "linux", "git_bash": False, "detail": out}
    if any(k in low for k in ("mingw", "msys", "cygwin")):
        return {"os": "windows", "git_bash": True, "detail": out}
    if not out:
        ver = ""
        try:
            ver = ssh_run(cfg, "ver")
        except Exception:
            ver = ""
        if not ver or "windows" not in ver.lower():
            try:
                alt = ssh_run(cfg, "cmd /c ver")
                if alt and "windows" in alt.lower():
                    ver = alt
            except Exception:
                pass
        if ver and "windows" in ver.lower():
            detail = ver
            pwsh_ver = ""
            for cmd in (
                'pwsh -NoProfile -Command "$PSVersionTable.PSVersion.ToString()"',
                'powershell -NoProfile -Command "$PSVersionTable.PSVersion.ToString()"',
            ):
                try:
                    pv = ssh_run(cfg, cmd)
                    if pv and pv.strip():
                        pwsh_ver = pv.strip().splitlines()[0].strip()
                        break
                except Exception:
                    continue
            if pwsh_ver:
                detail = f"{ver} | PowerShell {pwsh_ver}"
                if pwsh_ver.startswith("7."):
                    detail += " (PowerShell 7)"
            detail += " | 诊断: ssh -vvv %s@%s \"uname -s; ver\"" % (
                (cfg.get("ssh_user") or cfg.get("user") or "user"),
                (cfg.get("ssh_host") or "host"),
            )
            return {"os": "windows", "git_bash": False, "detail": detail}
    return {"os": "unknown", "git_bash": False, "detail": out or ""}


def build_tunnel_cmd(cfg, local_port):
    """构造 ssh 端口转发命令(纯函数,供测试与回显)。

    cfg 关键字段:
      ssh_host / ssh_port / ssh_user / ssh_key
      ssh_bind_host(空=cfg.host) / ssh_bind_port(空=cfg.port)
    local_port:本地转发端口。
    返回 ssh 命令行参数列表。
    """
    if not is_ssh_cfg(cfg):
        return []
    ssh_host = (cfg.get("ssh_host") or "").strip()
    if not ssh_host:
        raise ValueError("未配置 SSH 主机地址")
    port = int(cfg.get("ssh_port") or 22)
    user = (cfg.get("ssh_user") or "").strip() or (cfg.get("user") or "root")
    key = (cfg.get("ssh_key") or "").strip()
    bind_host = ((cfg.get("ssh_bind_host") or "").strip()
                 or (cfg.get("host") or "") or "127.0.0.1")
    bind_port = int(cfg.get("ssh_bind_port") or (cfg.get("port") or 3306))

    cmd = [
        "ssh", "-N", "-T",
        "-p", str(port),
        "-L", "127.0.0.1:%d:%s:%d" % (local_port, bind_host, bind_port),
        "-o", "StrictHostKeyChecking=no",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
    ]
    if key:
        if not os.path.isfile(key):
            raise ValueError("SSH 私钥文件不存在: %s" % key)
        cmd += ["-i", key]
    cmd += ["%s@%s" % (user, ssh_host)]
    return cmd


def start_tunnel(cfg):
    """开启 SSH 隧道(如启用)。

    返回 (tunnel_info, effective_cfg):
      tunnel_info: dict(proc, local_port) 或 None
      effective_cfg: 改写 host/port 为本地转发端点后的配置(未启用/失败=原样)
    抛出前确保无残留子进程与密钥泄漏。
    """
    if not is_ssh_cfg(cfg):
        return None, dict(cfg)
    if not ssh_available():
        raise RuntimeError(
            "SSH 隧道需要系统 ssh 命令,当前未找到。请确保 OpenSSH 客户端已安装"
            '(Windows:设置 → 应用 → 可选功能 → OpenSSH 客户端;Linux:apt install openssh-client)')
    local_port = pick_free_port(int(cfg.get("ssh_local_port") or 0))
    cmd = build_tunnel_cmd(cfg, local_port)
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if IS_WIN else 0,
            start_new_session=(not IS_WIN))
    except FileNotFoundError:
        raise RuntimeError("未找到 ssh 可执行文件")

    # 短暂等待端口就绪/失败(ExitOnForwardFailure 会立即退出)
    deadline = time.time() + 8
    while time.time() < deadline:
        if proc.poll() is not None:
            # 隧道启动即退出:收集原因
            err = _read_proc_err(proc)
            raise RuntimeError("SSH 隧道启动失败: %s" % (err or "ssh 异常退出"))
        if _port_open(local_port):
            break
        time.sleep(0.25)
    else:
        _terminate(proc)
        raise RuntimeError(
            "SSH 隧道未在 8 秒内就绪(%s:%d)。请检查 SSH 地址/端口/密钥与目标 3306 可达性"
            % (cfg.get("ssh_host", ""), local_port))

    with _TUNNELS_LOCK:
        _TUNNELS[local_port] = proc
    eff = dict(cfg)
    eff["host"] = "127.0.0.1"
    eff["port"] = local_port
    eff["_ssh_local_port"] = local_port
    return {"proc": proc, "local_port": local_port}, eff


def _port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _read_proc_err(proc):
    try:
        out, err = proc.communicate(timeout=2)
    except Exception:
        return ""
    return ((err or out) or b"").decode("utf-8", "replace").strip()


def stop_tunnel(info):
    """停止并清理一条隧道子进程(幂等)。"""
    if not info:
        return
    proc = info.get("proc")
    local_port = info.get("local_port")
    if proc:
        _terminate(proc)
    if local_port:
        with _TUNNELS_LOCK:
            _TUNNELS.pop(local_port, None)


def _terminate(proc):
    try:
        if proc.poll() is None:
            if IS_WIN:
                proc.terminate()
            else:
                try:
                    proc.terminate()
                except OSError:
                    pass
        if proc.poll() is None:
            proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=3)
    except Exception:
        pass


def ensure_tunnel_stopped():
    """停止全部隧道(进程退出/异常兜底)。"""
    with _TUNNELS_LOCK:
        infos = list(_TUNNELS.values())
        _TUNNELS.clear()
    for proc in infos:
        _terminate(proc)