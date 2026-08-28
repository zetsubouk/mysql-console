# -*- coding: utf-8 -*-
"""端到端验证(2026-08-28 对齐 R7 异步引擎):建测试库 -> API 异步备份(轮询) -> 清空数据
-> API 异步还原(轮询) -> 校验行数 -> 清理。

说明:备份/还原自 R7 起为异步任务(接口返回 202 + task_id),必须轮询 /api/task/<id>
到终态再读 result;旧版直接读 r['result'] 的同步写法已废弃,会导致 KeyError。
需连接已激活并服务已运行(CI 中由 workflow 的 e2e job 准备)。
"""
import json
import sys
import time
import urllib.request

sys.path.insert(0, ".")
import config_store
import pymysql

BASE = "http://127.0.0.1:8090"
cfg = config_store.get_connection(config_store.get_active_conn_id())


def api(path, body=None, timeout=600):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def wait_task(tid, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        t = api("/api/task/" + tid)
        if t["status"] in ("done", "failed"):
            return t
        time.sleep(0.5)
    raise TimeoutError("任务超时: " + tid)


conn = pymysql.connect(host=cfg["host"], port=cfg["port"],
                       user=cfg["user"], password=cfg["password"], autocommit=True)
cur = conn.cursor()

# 1. 建测试库
cur.execute("DROP DATABASE IF EXISTS test_verify")
cur.execute("CREATE DATABASE test_verify DEFAULT CHARACTER SET utf8mb4")
cur.execute("CREATE TABLE test_verify.t1 (id INT PRIMARY KEY, name VARCHAR(50))")
cur.execute("INSERT INTO test_verify.t1 VALUES (1,'a'),(2,'b'),(3,'c')")
print("1. 已创建测试库 test_verify(含表 t1,3 行数据)")

# 2. 异步备份 + 轮询到终态
r = api("/api/backup", {"dbs": ["test_verify"], "gzip": True})
assert r.get("ok") and r.get("task_id"), "备份任务未启动: %r" % r
t = wait_task(r["task_id"])
rec = t.get("result") or {}
print(f"2. 备份: {rec.get('result')} | 文件: {rec.get('path')} | 大小: {rec.get('size')}B | 耗时: {rec.get('elapsed')}s")
assert t["status"] == "done" and rec.get("result") == "success", "备份失败!"

# 3. 清空数据(模拟数据丢失)
cur.execute("DELETE FROM test_verify.t1")
print("3. 已清空 t1 数据(模拟数据丢失)")

# 4. 异步还原 + 轮询到终态
r2 = api("/api/restore", {"target_db": "test_verify", "file": rec["path"]})
assert r2.get("ok") and r2.get("task_id"), "还原任务未启动: %r" % r2
t2 = wait_task(r2["task_id"])
rec2 = t2.get("result") or {}
print(f"4. 还原: {rec2.get('result')} | 耗时: {rec2.get('elapsed')}s")
assert t2["status"] == "done" and rec2.get("result") == "success", "还原失败!"

# 5. 校验
cur.execute("SELECT COUNT(*) FROM test_verify.t1")
cnt = cur.fetchone()[0]
print(f"5. 还原后 t1 行数: {cnt} -> {'校验通过!' if cnt == 3 else '校验失败!'}")
assert cnt == 3

# 6. 清理
cur.execute("DROP DATABASE test_verify")
conn.close()
print("6. 已清理测试库 test_verify,端到端验证完成")