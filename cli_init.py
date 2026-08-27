# -*- coding: utf-8 -*-
"""一键初始化命令行入口(与 start/stop 平级)。

用法:
  python cli_init.py --check       检测当前环境并打印信息汇总(只读,不删任何东西)
  python cli_init.py --do [--force] 执行初始化清理(删除配置/系统库/备份)

流程(通常由 init.bat / init.sh 调用):
  1) --check  打印汇总 → 客户确认
  2) --do     执行清理:
       · 全量模式: 用 bootstrap 连接 DROP 系统配置库(如 _mysql_console)
       · 删除本地 data/config.db(+wal/shm) / config.json* / .secret.key / data/logs/*
       · 删除备份文件(配置的 backup_dir + 默认 data/backups)
       · 绝不碰被管理的生产库(如 ERP/OA 系统库等)

退出码: 0=成功, 1=失败或取消
"""
import argparse
import os
import sys
import shutil

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import local_store          # noqa: E402
import config_store         # noqa: E402
from mysql_client import drop_db     # noqa: E402

LINE = "=" * 60


# ---------------- 检测(只读) ----------------
def fmt(cfg):
    return "%s@%s:%s" % (cfg.get("user", "root"), cfg.get("host", "?"), cfg.get("port", "?"))


def _count_files(root):
    n = 0
    if root and os.path.isdir(root):
        for _r, _d, fs in os.walk(root):
            n += len(fs)
    return n


def check_info():
    """返回当前环境摘要 dict,并顺带打印。"""
    info = {"is_configured": local_store.get_meta("setup_done") == "1",
            "run_mode": local_store.get_meta("run_mode") or "",
            "sys_db_name": config_store._sys_db_name(),
            "config_db_exists": os.path.exists(local_store.DB_PATH),
            "secret_key_exists": os.path.exists(config_store.KEY_PATH),
            "port_running": _port_running(8090)}

    info["bootstrap"] = None
    boot = None
    try:
        boot = config_store._get_bootstrap_conn_cfg()
    except Exception:
        boot = None
    if boot:
        info["bootstrap"] = fmt(boot)

    # 系统库可达性(全量时)
    info["sys_db_usable"] = bool(config_store._is_full_config()) and config_store._system_db_usable()

    # 备份目录(尽力读取,读取失败不阻断检测)
    bdirs = []
    try:
        s = config_store.get_settings()
        if s.get("backup_dir"):
            bdirs.append(os.path.abspath(s["backup_dir"]))
    except Exception:
        pass
    default_bdir = os.path.join(BASE_DIR, "data", "backups")
    if os.path.abspath(default_bdir) not in [os.path.abspath(x) for x in bdirs]:
        bdirs.append(os.path.abspath(default_bdir))
    info["backup_dirs"] = bdirs
    info["backup_count"] = sum(_count_files(d) for d in bdirs)

    # 待删本地文件清单
    to_delete = _local_files_to_delete()
    info["local_files"] = to_delete
    return info


def _local_files_to_delete():
    """返回本次初始化会删除的本地 data 文件清单(全量删除范围,不含备份目录)。"""
    files = []
    # config.db 家族
    for suffix in ("", "-wal", "-shm"):
        p = local_store.DB_PATH + suffix
        if os.path.exists(p):
            files.append(p)
    # 各类 config.json 残留
    for name in os.listdir(local_store.DATA_DIR):
        if name.startswith("config.json"):
            files.append(os.path.join(local_store.DATA_DIR, name))
    # secret key
    if os.path.exists(config_store.KEY_PATH):
        files.append(config_store.KEY_PATH)
    # logs
    logs_dir = os.path.join(local_store.DATA_DIR, "logs")
    if os.path.isdir(logs_dir):
        for _r, _d, fs in os.walk(logs_dir):
            for f in fs:
                files.append(os.path.join(_r, f))
    return files


def _port_running(port):
    """检查端口是否有进程监听(仅 Windows 用 netstat; 其他平台忽略)。"""
    if os.name != "nt":
        return False
    import subprocess
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True,
                             shell=True, timeout=10)
        text = out.stdout.decode("gbk", errors="ignore")
        for line in text.splitlines():
            if ":%d" % port in line and "LISTENING" in line:
                return True
    except Exception:
        return False
    return False


def print_summary(info):
    print(LINE)
    print("  MySQL Console - 一键初始化 · 当前环境汇总")
    print(LINE)
    print("  [配置状态] %s" % ("已配置" if info["is_configured"] else "尚未配置(本身已干净)"))
    print("  [运行模式] %s" % ("全量(full)" if info["run_mode"] == "full"
                                else ("轻量(lite)" if info["run_mode"] else "未初始化(默认轻量)")))
    if info["run_mode"] == "full":
        print("  [系统库]   %s%s" % (info["sys_db_name"],
                               "  (可达)" if info["sys_db_usable"] else "  (不可达)"))
    if info["bootstrap"]:
        print("  [连接信息] %s" % info["bootstrap"])
    print("  [本地配置] %s" % ("config.db 存在" if info["config_db_exists"] else "config.db 不存在"))
    print("  [秘密密钥] %s" % ("存在" if info["secret_key_exists"] else "不存在"))
    print("  [8090端口] %s" % ("有进程在运行" if info["port_running"] else "无进程"))
    print("  [备份目录] %s 处, 备份文件 %d 个" % (len(info["backup_dirs"]), info["backup_count"]))
    for d in info["backup_dirs"]:
        print("        - %s" % d)
    print(LINE)
    print("  初始化将 <永久删除>:")
    for f in info["local_files"]:
        print("        - %s" % f)
    if info["run_mode"] == "full" and info["sys_db_name"]:
        print("        - 系统配置库 `%s` (DROP; 绝不影响生产库)" % info["sys_db_name"])
    if info["backup_count"]:
        print("        - %d 个历史备份文件" % info["backup_count"])
    print("  保留: 程序源码 / 依赖 / 生产数据(绝不动生产库)")
    print(LINE)


# ---------------- 执行清理 ----------------
def do_init(info):
    print("[1/4] 检查 8090 端口进程 ... (交由外层脚本处理端口清理)")
    if info.get("port_running"):
        print("  ! 注意: 8090 有进程在运行, init.bat 会先将其停止。")

    # ① 全量模式: DROP 系统配置库
    if info["run_mode"] == "full" and info["sys_db_name"]:
        boot = None
        try:
            boot = config_store._get_bootstrap_conn_cfg()
        except Exception:
            boot = None
        if boot:
            try:
                print("[2/4] 删除系统配置库 `%s` ..." % info["sys_db_name"])
                drop_db(boot, info["sys_db_name"])
                print("       - 已删除系统库 `%s`" % info["sys_db_name"])
            except Exception as e:
                print("       ! 删除系统库失败(库可能已不存在,忽略): %s" % e)
        else:
            print("       ! 无可用 bootstrap 连接,跳过系统库 DROP(仅清本地)")
    else:
        print("[2/4] 轻量模式: 无系统库需删除")

    # ② 删除本地配置文件
    print("[3/4] 删除本地配置文件 ...")
    for f in info["local_files"]:
        try:
            os.remove(f)
            print("       - 删除 %s" % f)
        except FileNotFoundError:
            pass
        except Exception as e:
            print("       ! 无法删除 %s: %s" % (f, e))

    # ③ 删除备份文件(保留目录本身)
    print("[4/4] 删除历史备份文件 ...")
    deleted = 0
    for root in info["backup_dirs"]:
        if not os.path.isdir(root):
            continue
        for _r, _d, fs in os.walk(root):
            for f in fs:
                p = os.path.join(_r, f)
                try:
                    os.remove(p)
                    deleted += 1
                except Exception as e:
                    print("       ! 无法删除 %s: %s" % (p, e))
    print("       - 已删除 %d 个备份文件" % deleted)

    print(LINE)
    print("  ✅ 初始化完成! 系统已恢复为全新状态。")
    print("  请打开 http://127.0.0.1:8090 重新完成首次配置。")
    print(LINE)
    return 0


def main():
    ap = argparse.ArgumentParser(description="MySQL Console 一键初始化")
    ap.add_argument("--check", action="store_true", help="仅检测并打印信息汇总(只读)")
    ap.add_argument("--do", action="store_true", help="执行初始化清理")
    ap.add_argument("--force", action="store_true", help="直接清理,再提示确认(供外层脚本二次兜底)")
    args = ap.parse_args()

    info = check_info()

    if args.check:
        print_summary(info)
        return 0

    if args.do:
        print_summary(info)
        if not args.force:
            print()
            print("以上数据将被永久删除,且无法恢复!")
            ans = input("确认初始化? 输入 y 确认 / N 取消: ").strip().lower()
            if ans != "y":
                print("已取消,未做任何改动。")
                return 1
        return do_init(info)

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())