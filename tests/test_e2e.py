# -*- coding: utf-8 -*-
"""端到端验证:创建测试库 -> API 备份 -> 清空数据 -> API 还原 -> 校验 -> 清理"""
import json
import sys
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


conn = pymysql.connect(host=cfg["host"], port=cfg["port"],
                       user=cfg["user"], password=cfg["password"], autocommit=True)
cur = conn.cursor()

# 1. 建测试库
cur.execute("DROP DATABASE IF EXISTS test_verify")
cur.execute("CREATE DATABASE test_verify DEFAULT CHARACTER SET utf8mb4")
cur.execute("CREATE TABLE test_verify.t1 (id INT PRIMARY KEY, name VARCHAR(50))")
cur.execute("INSERT INTO test_verify.t1 VALUES (1,'a'),(2,'b'),(3,'c')")
print("1. 已创建测试库 test_verify(含表 t1,3 行数据)")

# 2. API 备份
r = api("/api/backup", {"dbs": ["test_verify"], "gzip": True})
print(f"2. 备份: {r['result']} | 文件: {r['path']} | 大小: {r['size']}B | 耗时: {r['elapsed']}s")
assert r["result"] == "success", "备份失败!"

# 3. 清空数据(模拟数据丢失)
cur.execute("DELETE FROM test_verify.t1")
print("3. 已清空 t1 数据(模拟数据丢失)")

# 4. API 还原
r2 = api("/api/restore", {"target_db": "test_verify", "file": r["path"]})
print(f"4. 还原: {r2['result']} | 耗时: {r2['elapsed']}s")
assert r2["result"] == "success", "还原失败!"

# 5. 校验
cur.execute("SELECT COUNT(*) FROM test_verify.t1")
cnt = cur.fetchone()[0]
print(f"5. 还原后 t1 行数: {cnt} -> {'校验通过!' if cnt == 3 else '校验失败!'}")
assert cnt == 3

# 6. 清理
cur.execute("DROP DATABASE test_verify")
conn.close()
print("6. 已清理测试库 test_verify,端到端验证完成")
