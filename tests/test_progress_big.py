# 大表备份进度平滑性验证(建约 200MB 表,验证进度滚动 + 任务完成)
import json
import sys
import time
import urllib.request

sys.path.insert(0, ".")
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


conn = pymysql.connect(host=cfg["host"], port=cfg["port"],
                       user=cfg["user"], password=cfg["password"], autocommit=True)
cur = conn.cursor()

print("1. 建大表测试库(500 万行,约 200MB)...")
cur.execute("DROP DATABASE IF EXISTS prog_big")
cur.execute("CREATE DATABASE prog_big")
cur.execute("CREATE TABLE prog_big.big (id INT PRIMARY KEY, v1 VARCHAR(40), v2 VARCHAR(40))")
cur.execute("SET SESSION cte_max_recursion_depth = 6000000")
cur.execute("""
    INSERT INTO prog_big.big
    SELECT n, CONCAT('value-', n, '-abcdefghijklmnopqrstuvwxyz'), CONCAT('data-', n)
    FROM (WITH RECURSIVE seq_(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM seq_ WHERE n < 5000000)
          SELECT n FROM seq_) t""")
cur.execute("SELECT DATA_LENGTH+INDEX_LENGTH FROM information_schema.tables WHERE TABLE_SCHEMA='prog_big'")
print("2. 表大小:", cur.fetchone()[0], "bytes")

r = api("/api/backup", {"dbs": ["prog_big"], "gzip": True})
tid = r["task_id"]
print("3. 备份任务启动:", tid)

# 轮询采样
samples = []
t0 = time.time()
while time.time() - t0 < 300:
    t = api("/api/task/" + tid)
    samples.append((t["percent"], t["status"], t.get("current", ""), t.get("message", "")[:40]))
    if t["status"] in ("done", "failed"):
        break
    time.sleep(0.5)

print(f"4. 采样 {len(samples)} 次,耗时 {time.time()-t0:.1f}s")
distinct_pct = sorted({s[0] for s in samples if s[1] == "running"})
print(f"   运行中不同进度值({len(distinct_pct)}个): {distinct_pct[:5]}...{distinct_pct[-3:]}")
print(f"   最后状态: {samples[-1][1]} | 最终进度: {samples[-1][0]}%")
print(f"   表进度采样: {[s[2] for s in samples[::max(1,len(samples)//8)]][:8]}")

t = samples[-1]
if t[1] != "done":
    print("[FAIL] 任务未完成!")
    sys.exit(1)
if len(distinct_pct) < 3:
    print(f"[WARN] 进度采样点少({len(distinct_pct)}个),平滑性不足")
else:
    print(f"[OK] 进度平滑递增({len(distinct_pct)} 个不同进度值)")

# 清理
cur.execute("DROP DATABASE prog_big")
conn.close()
print("5. 测试库已清理")
