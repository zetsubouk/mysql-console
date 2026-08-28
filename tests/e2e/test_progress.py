# -*- coding: utf-8 -*-
"""异步备份/还原 + 进度轮询端到端验证"""
import json
import sys
import time
import urllib.request

import os
_WORKSPACE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_WORKSPACE, "src"))
import config_store
import pymysql

BASE = "http://127.0.0.1:8090"
cfg = config_store.get_connection(config_store.get_active_conn_id())


def api(path, body=None, timeout=30):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def wait_task(tid, expect="done", timeout=120):
    """轮询任务直到终态,返回任务与进度采样。"""
    samples = []
    t0 = time.time()
    while time.time() - t0 < timeout:
        t = api("/api/task/" + tid)
        samples.append((t["percent"], t["phase"], t.get("current", "")))
        if t["status"] in ("done", "failed"):
            return t, samples
        time.sleep(0.4)
    raise TimeoutError("任务超时")


conn = pymysql.connect(host=cfg["host"], port=cfg["port"],
                       user=cfg["user"], password=cfg["password"], autocommit=True)
cur = conn.cursor()

# 1. 建测试库(含一张 8 万行表,便于观察中间进度)
cur.execute("DROP DATABASE IF EXISTS test_pg")
cur.execute("CREATE DATABASE test_pg DEFAULT CHARACTER SET utf8mb4")
cur.execute("CREATE TABLE test_pg.big (id INT PRIMARY KEY, v VARCHAR(60))")
cur.execute("SET SESSION cte_max_recursion_depth = 100000")
cur.execute("""
    INSERT INTO test_pg.big
    SELECT seq, CONCAT('row-', seq) FROM (
      WITH RECURSIVE seq_(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM seq_ WHERE n < 80000)
      SELECT n AS seq FROM seq_) t""")
cur.execute("CREATE TABLE test_pg.small (id INT PRIMARY KEY, name VARCHAR(20))")
cur.execute("INSERT INTO test_pg.small VALUES (1,'a'),(2,'b')")
print("1. 测试库 test_pg 已建(big 8万行 + small 2行)")

# 2. 异步备份
r = api("/api/backup", {"dbs": ["test_pg"], "gzip": True})
print("2. 备份任务已启动:", r)
t, samples = wait_task(r["task_id"])
print(f"3. 备份结果: status={t['status']} 最终进度={t['percent']}% 耗时={t['elapsed']}s")
print(f"   进度采样({len(samples)}次): {samples[:4]} ... {samples[-2:]}")
assert t["status"] == "done" and t["result"]["result"] == "success", "备份任务失败!"
assert t["result"]["path"].endswith(".sql.gz"), f"扩展名应为 .sql.gz: {t['result']['path']}"
print("   备份文件:", t["result"]["path"], "| 大小:", t["result"]["size"])

# 3. 清空数据
cur.execute("DELETE FROM test_pg.big")
print("4. 已清空 big 表数据")

# 4. 异步还原
r2 = api("/api/restore", {"target_db": "test_pg", "file": t["result"]["path"]})
print("5. 还原任务已启动:", r2)
t2, samples2 = wait_task(r2["task_id"])
print(f"6. 还原结果: status={t2['status']} 最终进度={t2['percent']}% 耗时={t2['elapsed']}s")
assert t2["status"] == "done" and t2["result"]["result"] == "success", "还原任务失败!"

# 5. 校验
cur.execute("SELECT COUNT(*) FROM test_pg.big")
cnt = cur.fetchone()[0]
print(f"7. 还原后 big 行数: {cnt} -> {'校验通过!' if cnt == 80000 else '校验失败!'}")
assert cnt == 80000

# 6. 清理
cur.execute("DROP DATABASE test_pg")
conn.close()
print("8. 已清理测试库,异步任务进度验证完成")
