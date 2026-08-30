# -*- coding: utf-8 -*-
"""MySQL 连接与查询封装:服务器状态、数据库统计、用户、进程列表。"""
import re
import pymysql


class DbError(Exception):
    pass


def connect(conn_cfg, timeout=5, database=None):
    """按配置建立连接,返回 pymysql 连接对象。可通过 database 指定默认库(等价 USE 该库)。"""
    try:
        return pymysql.connect(
            host=conn_cfg.get("host", "127.0.0.1"),
            port=int(conn_cfg.get("port", 3306)),
            user=conn_cfg.get("user", "root"),
            password=conn_cfg.get("password", ""),
            database=database or None,
            connect_timeout=timeout,
            read_timeout=30,
            write_timeout=30,
            charset="utf8mb4",
        )
    except pymysql.MySQLError as e:
        raise DbError(f"连接失败: {e.args[1] if len(e.args) > 1 else e}")


def test(conn_cfg):
    conn = connect(conn_cfg)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT VERSION()")
            return {"ok": True, "version": cur.fetchone()[0]}
    finally:
        conn.close()


def db_exists(conn_cfg, db_name):
    """判断指定库是否存在于服务器。db_name 应为合法标识符(由调用方校验)。"""
    conn = connect(conn_cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = %s",
                (db_name,),
            )
            return cur.fetchone() is not None
    finally:
        conn.close()


def drop_db(conn_cfg, db_name):
    """删除指定库。仅用于引导界面用户显式确认后清理遗留的系统库。"""
    conn = connect(conn_cfg)
    try:
        with conn.cursor() as cur:
            cur.execute("DROP DATABASE IF EXISTS `%s`" % db_name)
        conn.commit()
    finally:
        conn.close()


def _q(conn, sql, args=None):
    with conn.cursor() as cur:
        cur.execute(sql, args)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchall()
    return cols, rows


def _q1(conn, sql):
    with conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
    return row


# ---------------- 只读 SQL 查询执行器 ----------------
# 仅允许 SELECT 类只读语句,拦截 INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE 等写操作,
# 并统一限制返回行数,防误操作破坏与超大结果集拖垮前端。

# 语句前导关键字白名单(忽略前导空白/注释与大小写)
_READ_KEYWORDS = {
    "SELECT", "SHOW", "DESC", "DESCRIBE", "EXPLAIN", "WITH",
    "VALUES", "TABLE", "HELP", "SET",
}
# 显式列入白名单但仍需二次校验的危险/特殊关键字(逐条拒绝)
_WRITE_KEYWORDS = {
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE",
    "REPLACE", "GRANT", "REVOKE", "RENAME", "CALL", "LOAD",
}
QUERY_MAX_ROWS = 500  # 默认返回行数上限

_RE_LEAD = re.compile(r"^\s*(?:--[^\n]*\n|#.*?$|/\*.*?\*/\s*)*([A-Za-z]+)", re.S)


def _query_leading_keyword(sql):
    """提取 SQL 的首个字母关键字(剥离前导空白/注释),无则返回空串。"""
    m = _RE_LEAD.match(sql or "")
    return m.group(1).upper() if m else ""


def run_query(conn, sql, max_rows=None):
    """执行只读 SQL 并返回结果。

    - 语句必须命中只读白名单,否则抛 DbError(拒绝写操作)。
    - 单条语句执行,返回 {columns, rows, truncated, affected, elapsed}。
    - `max_rows` 默认 QUERY_MAX_ROWS,超出部分截断并以 truncated 标记。
    """
    import time
    if max_rows is None:
        max_rows = QUERY_MAX_ROWS
    kw = _query_leading_keyword(sql)
    if not kw:
        raise DbError("空语句")
    if kw in _WRITE_KEYWORDS or kw not in _READ_KEYWORDS:
        raise DbError(f"仅允许只读查询(SELECT/SHOW/DESC/EXPLAIN/WITH),语句以 {kw or '(空)'} 开头被拒绝")

    t0 = time.time()
    result = {"columns": [], "rows": [], "truncated": False, "affected": 0, "elapsed": 0.0}
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            if cur.description:
                result["columns"] = [d[0] for d in cur.description]
                result["rows"] = cur.fetchmany(max_rows)
                result["truncated"] = len(result["rows"]) >= max_rows and cur.fetchone() is not None
            else:
                result["affected"] = cur.rowcount
    finally:
        result["elapsed"] = round(time.time() - t0, 3)
    return result


def kill_query(conn, pid):
    """终止指定 ID 正在执行的查询(SELECT 可被杀断)。"""
    conn.cursor().execute(f"KILL QUERY {int(pid)}")
    conn.commit()


def server_overview(conn):
    """服务器概览信息。"""
    status = {}
    vars_ = {}
    with conn.cursor() as cur:
        cur.execute("SHOW GLOBAL STATUS")
        for k, v in cur.fetchall():
            status[k] = v
        cur.execute("SHOW GLOBAL VARIABLES")
        for k, v in cur.fetchall():
            vars_[k] = v

    uptime = int(status.get("Uptime", 0))
    days, rem = divmod(uptime, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    return {
        "version": vars_.get("version", ""),
        "hostname": vars_.get("hostname", ""),
        "port": vars_.get("port", ""),
        "datadir": vars_.get("datadir", ""),
        "charset": vars_.get("character_set_server", ""),
        "uptime_text": f"{days}天 {hours}时 {mins}分",
        "uptime_sec": uptime,
        "current_conn": int(status.get("Threads_connected", 0)),
        "max_conn": int(vars_.get("max_connections", 0)),
        "qps": int(status.get("Questions", 0)),
        "threads_running": int(status.get("Threads_running", 0)),
        "threads_created": int(status.get("Threads_created", 0)),
        "aborted_conn": int(status.get("Aborted_connects", 0)),
        "innodb_buffer_hits": int(status.get("Innodb_buffer_pool_read_requests", 0)),
        "innodb_buffer_reads": int(status.get("Innodb_buffer_pool_reads", 0)),
        "innodb_buffer_size": vars_.get("innodb_buffer_pool_size", "0"),
        "slow_queries": int(status.get("Slow_queries", 0)),
        "open_tables": int(status.get("Open_tables", 0)),
        "opened_tables": int(status.get("Opened_tables", 0)),
    }


def database_list(conn):
    """数据库列表:表数量、数据/索引/总大小。"""
    cols, rows = _q(conn, """
        SELECT t.TABLE_SCHEMA, COUNT(DISTINCT t.TABLE_NAME),
               IFNULL(SUM(t.DATA_LENGTH),0), IFNULL(SUM(t.INDEX_LENGTH),0),
               s.DEFAULT_CHARACTER_SET_NAME
        FROM information_schema.tables t
        LEFT JOIN information_schema.schemata s ON s.SCHEMA_NAME = t.TABLE_SCHEMA
        WHERE t.TABLE_SCHEMA NOT IN ('information_schema','performance_schema','mysql','sys')
        GROUP BY t.TABLE_SCHEMA, s.DEFAULT_CHARACTER_SET_NAME
        ORDER BY SUM(t.DATA_LENGTH) + SUM(t.INDEX_LENGTH) DESC
    """)
    out = []
    for r in rows:
        total = (r[2] or 0) + (r[3] or 0)
        out.append({
            "name": r[0],
            "table_count": int(r[1] or 0),
            "data_size": int(r[2] or 0),
            "index_size": int(r[3] or 0),
            "total_size": int(total),
            "charset": r[4] or "",
        })
    return out


def database_detail(conn, db_name):
    """指定库的表列表。"""
    cols, rows = _q(conn, """
        SELECT table_name, engine, table_rows,
               data_length, index_length, create_time, table_comment
        FROM information_schema.tables
        WHERE table_schema = %s
        ORDER BY table_name
    """, (db_name,))
    out = []
    for r in rows:
        out.append({
            "name": r[0], "engine": r[1] or "", "rows": int(r[2] or 0),
            "data_size": int(r[3] or 0), "index_size": int(r[4] or 0),
            "create_time": str(r[5]) if r[5] else "", "comment": (r[6] or "")[:60],
        })
    return out


def user_list(conn):
    """用户列表与权限概览。"""
    cols, rows = _q(conn, """
        SELECT user, host,
               IF(authentication_string='' AND plugin IN ('mysql_native_password','caching_sha2_password'), 'NO', 'YES') AS has_pwd,
               account_locked, plugin,
               (SELECT COUNT(*) FROM information_schema.user_privileges up
                WHERE up.grantee = CONCAT('\\'', user, '\\'@\\'', host, '\\'')) AS priv_count
        FROM mysql.user
        ORDER BY user
    """)
    out = []
    for r in rows:
        out.append({
            "user": r[0], "host": r[1], "has_password": r[2],
            "locked": r[3], "plugin": r[4], "privileges": int(r[5] or 0),
        })
    return out


def process_list(conn):
    """当前所有连接。"""
    cols, rows = _q(conn, "SHOW FULL PROCESSLIST")
    out = []
    for r in rows:
        d = dict(zip(cols, r))
        out.append({
            "id": d.get("Id"), "user": d.get("User"), "host": d.get("Host"),
            "db": d.get("db"), "command": d.get("Command"), "time": d.get("Time"),
            "state": d.get("State"), "info": (d.get("Info") or "")[:200],
        })
    return out


def kill_connection(conn, pid):
    conn.cursor().execute(f"KILL {int(pid)}")
    conn.commit()


def monitor_metrics(conn):
    """监控轮询指标(连接数、QPS、线程)。"""
    s1 = dict(_q(conn, "SHOW GLOBAL STATUS")[1])
    import time
    time.sleep(1)
    s2 = dict(_q(conn, "SHOW GLOBAL STATUS")[1])
    qps = (int(s2.get("Questions", 0)) - int(s1.get("Questions", 0)))
    return {
        "ts": time.time(),
        "connections": int(s2.get("Threads_connected", 0)),
        "running": int(s2.get("Threads_running", 0)),
        "qps": max(qps, 0),
        "slow": int(s2.get("Slow_queries", 0)),
    }


def monitor_full(conn):
    """合并轮询指标:连接/QPS + InnoDB 深度 + 复制延迟。

    一次 1s 双采样(与 monitor_metrics 相同开销),顺带计算:
    Buffer Pool 命中率、脏页比例、行锁等待增量、InnoDB 数据读写速率(KB/s)。
    复制延迟:非从库返回 None。
    """
    import time
    s1 = dict(_q(conn, "SHOW GLOBAL STATUS")[1])
    time.sleep(1)
    s2 = dict(_q(conn, "SHOW GLOBAL STATUS")[1])
    now = time.time()

    def _delta(key):
        try:
            return int(s2.get(key, 0)) - int(s1.get(key, 0))
        except Exception:
            return 0

    # 缓冲池命中率
    reads = int(s2.get("Innodb_buffer_pool_read_requests", 0))
    physical = int(s2.get("Innodb_buffer_pool_reads", 0))
    hit_rate = ((reads - physical) / reads * 100) if reads > 0 else 100
    # 脏页比例
    dirty = int(s2.get("Innodb_buffer_pool_pages_dirty", 0))
    total_p = int(s2.get("Innodb_buffer_pool_pages_total", 0))
    dirty_ratio = (dirty / total_p * 100) if total_p > 0 else 0

    # 复制状态(非从库 is_slave=False)
    repl = {"is_slave": False, "seconds_behind": None, "io_running": None, "sql_running": None}
    try:
        cols, rows = _q(conn, "SHOW SLAVE STATUS")
        if rows:
            d = dict(zip(cols, rows[0]))
            sb = d.get("Seconds_Behind_Master")
            repl.update(
                is_slave=True,
                io_running=str(d.get("Slave_IO_Running", "")),
                sql_running=str(d.get("Slave_SQL_Running", "")),
                seconds_behind=None if sb in (None, "NULL") else int(sb),
            )
    except Exception:
        pass

    return {
        "ts": now,
        "connections": int(s2.get("Threads_connected", 0)),
        "running": int(s2.get("Threads_running", 0)),
        "qps": max(_delta("Questions"), 0),
        "slow": int(s2.get("Slow_queries", 0)),
        "innodb": {
            "hit_rate": round(hit_rate, 2),
            "dirty_ratio": round(dirty_ratio, 2),
            "lock_waits": max(_delta("Innodb_row_lock_waits"), 0),
            "read_kbs": round(max(_delta("Innodb_data_read") / 1024, 0), 1),
            "write_kbs": round(max(_delta("Innodb_data_written") / 1024, 0), 1),
        },
        "repl": repl,
    }


def active_connections_by_db(conn):
    """按库统计当前连接数。"""
    cols, rows = _q(conn, """
        SELECT IFNULL(db,'(未选库)') AS db, COUNT(*) AS cnt
        FROM information_schema.processlist
        GROUP BY db ORDER BY cnt DESC
    """)
    return [{"name": r[0], "count": int(r[1])} for r in rows]


def health_score(conn):
    """计算服务器健康评分(0-100)及分项指标。"""
    status = {}
    with conn.cursor() as cur:
        cur.execute("SHOW GLOBAL STATUS")
        for k, v in cur.fetchall():
            status[k] = v

    # 缓冲池命中率
    reads = int(status.get("Innodb_buffer_pool_read_requests", 0))
    physical = int(status.get("Innodb_buffer_pool_reads", 0))
    hit_rate = ((reads - physical) / reads * 100) if reads > 0 else 100

    # 连接数使用率
    max_conn = int(status.get("Max_used_connections", 0))
    threads = int(status.get("Threads_connected", 0))

    # 慢查询
    slow = int(status.get("Slow_queries", 0))
    uptime = int(status.get("Uptime", 1))
    slow_per_hour = slow / (uptime / 3600) if uptime > 0 else 0

    # 线程运行数
    running = int(status.get("Threads_running", 0))

    # 计算评分
    score = 100
    if hit_rate < 90: score -= int((90 - hit_rate) * 2)
    elif hit_rate < 95: score -= int((95 - hit_rate) * 0.5)
    if slow_per_hour > 10: score -= 10
    elif slow_per_hour > 5: score -= 5
    if running > 20: score -= 10
    elif running > 10: score -= 5
    if threads > 100: score -= 5
    score = max(0, min(100, score))

    label = "优秀" if score >= 90 else "良好" if score >= 75 else "一般" if score >= 60 else "较差"
    return {
        "score": score,
        "label": label,
        "items": [
            {"label": "缓冲池命中率", "value": f"{hit_rate:.1f}%", "ok": hit_rate >= 95},
            {"label": "当前连接", "value": threads, "ok": threads < 100},
            {"label": "活跃线程", "value": running, "ok": running < 10},
            {"label": "慢查询/时", "value": f"{slow_per_hour:.1f}", "ok": slow_per_hour < 5},
        ]
    }


def innodb_metrics(conn):
    """InnoDB 引擎关键指标。"""
    status = {}
    with conn.cursor() as cur:
        cur.execute("SHOW GLOBAL STATUS")
        for k, v in cur.fetchall():
            status[k] = v

    reads = int(status.get("Innodb_buffer_pool_read_requests", 0))
    physical = int(status.get("Innodb_buffer_pool_reads", 0))
    hit_rate = ((reads - physical) / reads * 100) if reads > 0 else 100

    return {
        "hit_rate": f"{hit_rate:.1f}%",
        "rows_read": int(status.get("Innodb_rows_read", 0)),
        "rows_inserted": int(status.get("Innodb_rows_inserted", 0)),
        "rows_updated": int(status.get("Innodb_rows_updated", 0)),
        "rows_deleted": int(status.get("Innodb_rows_deleted", 0)),
        "lock_waits": int(status.get("Innodb_row_lock_waits", 0)),
        "lock_time_avg": int(status.get("Innodb_row_lock_time_avg", 0)),
    }


def tablespace_top(conn, limit=10):
    """表空间 Top N。"""
    cols, rows = _q(conn, """
        SELECT table_schema, table_name,
               data_length + index_length AS total_size,
               data_length, index_length, table_rows
        FROM information_schema.tables
        WHERE table_schema NOT IN ('information_schema','performance_schema','mysql','sys')
          AND table_type = 'BASE TABLE'
        ORDER BY total_size DESC
        LIMIT %s
    """, (limit,))
    return [
        {
            "db": r[0], "name": r[1],
            "total_size": int(r[2] or 0),
            "data_size": int(r[3] or 0),
            "index_size": int(r[4] or 0),
            "rows": int(r[5] or 0),
        }
        for r in rows
    ]


def replication_status(conn):
    """复制状态。"""
    try:
        cols, rows = _q(conn, "SHOW SLAVE STATUS")
        if not rows:
            return {"is_slave": False, "message": "当前服务器未配置为从库"}
        d = dict(zip(cols, rows[0]))
        return {
            "is_slave": True,
            "io_running": d.get("Slave_IO_Running", ""),
            "sql_running": d.get("Slave_SQL_Running", ""),
            "seconds_behind": d.get("Seconds_Behind_Master", "NULL"),
            "master_host": d.get("Master_Host", ""),
            "last_error": d.get("Last_Error", ""),
        }
    except Exception as e:
        return {"is_slave": False, "message": f"检测失败: {e}"}


def alerts(conn, max_conn=100, max_slow=10, max_running=20):
    """检查告警条件。阈值由调用方传入(默认值兼容旧调用)。"""
    status = {}
    with conn.cursor() as cur:
        cur.execute("SHOW GLOBAL STATUS")
        for k, v in cur.fetchall():
            status[k] = v

    threads = int(status.get("Threads_connected", 0))
    slow = int(status.get("Slow_queries", 0))
    uptime = int(status.get("Uptime", 1))
    slow_per_hour = slow / (uptime / 3600) if uptime > 0 else 0
    running = int(status.get("Threads_running", 0))

    alerts_list = []
    if threads > max_conn:
        alerts_list.append({"level": "warning", "message": f"连接数过高: {threads} (阈值 {max_conn})"})
    if slow_per_hour > max_slow:
        alerts_list.append({"level": "warning", "message": f"慢查询过多: {slow_per_hour:.1f}/小时 (阈值 {max_slow})"})
    if running > max_running:
        alerts_list.append({"level": "critical", "message": f"活跃线程过多: {running} (阈值 {max_running})"})

    return {"alerts": alerts_list, "checked_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S")}


def variables(conn):
    """获取服务器变量(含中文含义, 来自 variable_docs 词典)。"""
    from variable_docs import describe
    with conn.cursor() as cur:
        cur.execute("SHOW VARIABLES")
        rows = cur.fetchall()
    return [{"name": r[0], "value": r[1], "desc": describe(r[0])} for r in rows]
# ---------------- 用户管理(建/删/改密/授权, 2026-08-27) ----------------
# 标识符一律白名单正则校验后拼接, 杜绝注入(GRANT ... ON db.* 的库名无法参数化只能拼接)。
# PyMySQL 用 Python % 格式化, 故内联的通配主机 % 须由 _qh 写成 %%;密码走 %s 参数绑定。
_ALLOWED_PRIVS = {
    "SELECT", "INSERT", "UPDATE", "DELETE",               # DML
    "CREATE", "DROP", "ALTER", "INDEX", "REFERENCES",     # DDL
    "CREATE VIEW", "SHOW VIEW", "TRIGGER", "EVENT",
    "LOCK TABLES", "GRANT OPTION",
}
_PRESET = {
    "readonly": ["SELECT"],
    "dataentry": ["SELECT", "INSERT", "UPDATE", "DELETE"],   # 增删改查
    "struct": ["SELECT", "INSERT", "UPDATE", "DELETE",
               "CREATE", "ALTER", "DROP", "INDEX"],
    "all": ["ALL PRIVILEGES"],
}


def _clean(s):
    s = str(s).replace("\\", "").replace("'", "")
    return s


def _qh(host):
    """主机段转义：PyMySQL 用 Python % 格式化，字面 %(通配主机 %) 需写成 %%。"""
    return str(host).replace("%", "%%")


def _exec(conn, sql, args=(), op="操作"):
    """执行单条写语句，pymysql 错误统一转 DbError 便于前端友好提示。"""
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
        conn.commit()
    except pymysql.MySQLError as e:
        detail = e.args[1] if len(e.args) > 1 else str(e)
        raise DbError(f"{op}失败: {detail}")


def _validate_user(user):
    if not re.fullmatch(r"[A-Za-z0-9_@.\-]{1,64}", _clean(user)):
        raise DbError("非法的用户名(仅字母数字 _ @ . -)")


def _validate_host(host):
    if not re.fullmatch(r"[A-Za-z0-9_.\%\-]{1,64}", str(host)):
        raise DbError("非法的授权主机")


def _validate_db(db):
    if db != "*" and not re.fullmatch(r"[A-Za-z0-9_]{1,64}", db):
        raise DbError("非法的数据库名")


def create_user(conn, user, host, password):
    """创建 MySQL 用户。host 常见 %(任意)/localhost。"""
    user = _clean(user)
    _validate_user(user)
    _validate_host(host)
    if not password:
        raise DbError("新建用户必须设置密码")
    _exec(conn, f"CREATE USER '{user}'@'{_qh(host)}' IDENTIFIED BY %s", (password,), "创建用户")


def drop_user(conn, user, host):
    """删除 MySQL 用户(连同全部权限)。"""
    user = _clean(user)
    _validate_user(user)
    _validate_host(host)
    _exec(conn, f"DROP USER IF EXISTS '{user}'@'{_qh(host)}'", (), "删除用户")


def change_user_password(conn, user, host, new_password):
    """修改 MySQL 用户密码。"""
    user = _clean(user)
    _validate_user(user)
    _validate_host(host)
    if not new_password:
        raise DbError("密码不能为空")
    _exec(conn, f"ALTER USER '{user}'@'{_qh(host)}' IDENTIFIED BY %s", (new_password,), "修改密码")


def grant_privileges(conn, user, host, db, privileges):
    """对指定库(或 * 全部)授权。privileges 为权限名列表, 自动过滤非法权限。"""
    user = _clean(user)
    _validate_user(user)
    _validate_host(host)
    _validate_db(db)
    privs = [str(p).strip().upper() for p in privileges if str(p).strip().upper() in _ALLOWED_PRIVS]
    if not privs:
        raise DbError("未选择有效权限")
    if "ALL PRIVILEGES" in privs or "ALL" in privs:
        privs = ["ALL PRIVILEGES"]
    grant_opt = "GRANT OPTION" in privs
    privs = [p for p in privs if p != "GRANT OPTION"]
    grants = ", ".join(privs)
    dbobj = "*.*" if db == "*" else f"`{db}`.*"
    with_grant = " WITH GRANT OPTION" if grant_opt else ""
    _exec(conn, f"GRANT {grants} ON {dbobj} TO '{user}'@'{_qh(host)}'{with_grant}", (), "授权")


def revoke_all_db(conn, user, host, db):
    """撤销某库(或 *)上该用户的全部显式权限。编辑授权时先撤销再重授。"""
    user = _clean(user)
    _validate_user(user)
    _validate_host(host)
    _validate_db(db)
    dbobj = "*.*" if db == "*" else f"`{db}`.*"
    _exec(conn, f"REVOKE ALL PRIVILEGES ON {dbobj} FROM '{user}'@'{_qh(host)}'", (), "撤销授权")


def show_grants(conn, user, host):
    """返回 SHOW GRANTS FOR 的可读行列表。"""
    user = _clean(user)
    _validate_user(user)
    _validate_host(host)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SHOW GRANTS FOR '{user}'@'{_qh(host)}'", ())
            return [r[0] for r in cur.fetchall()]
    except pymysql.MySQLError as e:
        detail = e.args[1] if len(e.args) > 1 else str(e)
        raise DbError(f"查询授权失败: {detail}")
