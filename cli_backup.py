# -*- coding: utf-8 -*-
"""定时备份命令行执行入口:供系统计划任务(schtasks/cron/systemd timer)调用。

用法:
  python cli_backup.py --task <id>    执行指定任务
  python cli_backup.py --list         列出所有任务
退出码: 0=成功或跳过, 1=执行失败
"""
import argparse
import sys
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import config_store      # noqa: E402
import backup_engine     # noqa: E402
import schedule_store    # noqa: E402


def _backup_dir(task):
    return task.get("backup_dir") or config_store.get_settings().get("backup_dir") or None


def _prune(task):
    keep = int(task.get("keep", 7))
    bdir = os.path.abspath(_backup_dir(task) or "")
    records = backup_engine.list_backups()
    mine = [r for r in records if r["type"] == "backup"
            and str(r.get("path", "")).startswith(bdir)]
    for r in mine[:-keep] if len(mine) > keep else []:
        try:
            if os.path.exists(r["path"]):
                os.remove(r["path"])
            backup_engine.delete_backup_record(r["id"])
        except Exception:
            pass


def run_task(tid):
    task = schedule_store.get_task(tid)
    if not task:
        print(f"[cli_backup] 任务不存在: {tid}")
        return 1
    if not task.get("enabled"):
        print(f"[cli_backup] 任务未启用,跳过: {task['name']}")
        return 0
    cfg = config_store.get_connection(task.get("conn_id")) if task.get("conn_id") else None
    if not cfg:
        print(f"[cli_backup] 连接不可用: {task.get('conn_id')}")
        schedule_store.update_run_status(tid, "failed")
        return 1
    try:
        record = backup_engine.run_backup(
            cfg, task.get("dbs") or [], backup_dir=_backup_dir(task), gzip_=True)
        ok = record.get("result") == "success"
        schedule_store.update_run_status(tid, "success" if ok else "failed")
        print(f"[cli_backup] [{task['name']}] {record.get('result')}: {record.get('path')}")
        if ok:
            _prune(task)
        return 0 if ok else 1
    except Exception as e:
        schedule_store.update_run_status(tid, "failed")
        print(f"[cli_backup] 执行异常: {e}")
        return 1


def main():
    ap = argparse.ArgumentParser(description="MySQL Console 定时备份 CLI")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--task", help="执行指定 id 的任务")
    g.add_argument("--list", action="store_true", help="列出所有任务")
    args = ap.parse_args()
    if args.list:
        for t in schedule_store.list_tasks():
            status = "启用" if t["enabled"] else "停用"
            print(f"{t['id']}  [{status}] {t['name']}  {schedule_store.describe(t)}")
        return
    sys.exit(run_task(args.task))


if __name__ == "__main__":
    main()
