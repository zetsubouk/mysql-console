# 03 · 关键类与函数说明

> 精读指南：按"传输层 → 业务层 → 备份引擎 → 存储 → 平台能力"组织。行号链接指向当前源码。

## 1. 传输层（server.py）

### 类 `Handler`（[L49](../../src/server.py#L49)）

多重继承 `handlers.HandlerBase + http.server.BaseHTTPRequestHandler`，既是 HTTP 端点又是路由目标。

| 方法 | 位置 | 职责 |
|---|---|---|
| `log_message` / `handle_error` | L52/L55 | 静默访问日志；捕获客户端断开（WinError 10053/10054、BrokenPipe）不打印堆栈 |
| `_send_json` | L66 | JSON 输出，`default=str` 全局兜底 datetime，响应头 `no-store` |
| `_send_error` | L80 | 错误 JSON（`{ok:false, error}`） |
| `_read_body` | L83 | 按 Content-Length 读 body 并 json 解析，失败返回 `{}`（不抛） |
| `_auth_guard` | L93 | **三层守卫编排**：访问令牌门 → 免认证判断 → 会话认证，未通过即 401 |
| `_serve_static` | L106 | 静态文件服务，`normpath + startswith` 防路径穿越 |
| `_serve_download` | L125 | 备份文件流式下载，64KB 分块防大文件占内存 |
| `do_HEAD/do_GET` | L150/L153 | HEAD 复用 GET；GET：非 `/api/` → 静态，否则认证 → `_route_get` |
| `do_POST` | L168 | 认证 → CSRF → `_route_post` |
| `do_PUT` | L183 | **内联路由**：`/api/connections/<id>`（保存）、`/api/schedules/<id>`（保存 + native→builtin 自动反注册）、`/api/users/<u>@<h>`（更新）、`/api/settings`（access_token 单独走 Fernet 加密写入） |
| `do_DELETE` | L231 | **内联路由**：连接删除、任务删除（native 先反注册）、用户删除、备份记录删除 |

### 函数 `main()`（[L265-302](../../src/server.py#L265-L302)）

启动顺序（顺序有讲究）：
1. stdout/stderr 重配置 UTF-8（防 Windows ANSI 编码输出中文横幅即崩）；
2. 建 `DATA_DIR`；
3. **先**启动 3 个守护线程（scheduler_loop / _update_loop / _alert_history_loop）；
4. 部署安全门槛：绑定非回环且未设访问令牌 → **拒绝启动**；无 TLS → 打印明文警告；
5. `ThreadingHTTPServer((HOST, PORT), Handler)`；`MC_TLS=1` 时 `security.wrap_socket` 包装；
6. `serve_forever()`，Ctrl+C 优雅退出。

## 2. 路由层（routes.py）

### 函数 `dispatch(self, method, path, body=None)`（[L140-153](../../src/routes.py#L140-L153)）

- POST 只查精确表 `_POST_EXACT`；GET 先精确表再前缀表（**按前缀长度降序**，保证 `/api/schedules/env` 优先于 `/api/schedules/` 命中）；
- `_invoke()` 用 `getattr(self, 方法名)` 反射调用；`argkind` 决定签名：`none→fn()` / `body→fn(body)` / `path→fn(path)`；
- 未命中返回 False，由调用方回 404「未知接口」。

> 设计意图：handler 方法名以**字符串**存于路由表，路由表零依赖纯数据；PUT/DELETE 尚未纳入（见架构文档 §11）。

## 3. 业务层（handlers.py）

### 类 `HandlerBase`（[L534](../../src/handlers.py#L534)）

业务层基类，承载约 60 个处理器。命名约定即分类：`g_*`（GET 只读）、`p_*`（POST 动作）、`_handle_*`（登录/SQL/AI/用户等复合业务）。

| 成员 | 位置 | 职责 |
|---|---|---|
| `_route_get` / `_route_post` | L538-545 | 入口收敛：调 `routes.dispatch`，POST 先 `_read_body()`；未命中 404 |
| `_current_user` / `_log_op` | L548/L556 | 当前会话用户；操作日志（full 模式落 mc_operation_log） |
| `_check_access_token` | L94-105 | 访问令牌门；仅 `/api/security/info` 豁免；未设令牌时回 503 |
| `_is_auth_required` / `_check_auth` | L71-91 | 未设管理员密码全免认证；7 条白名单外要求 `Bearer` 会话（8h 过期即删） |
| `_check_csrf` | L108-117 | 写请求 Origin/Host 同源校验 |
| `_get_conn` / `_set_active_conn` | L378-394 | 激活连接内存 + 持久化双写（`_lock` 保护） |
| `scheduler_loop` | [L482-528](../../src/handlers.py#L482-L528) | **内置备份调度器**：20s 轮询；hourly 按 `last_run` 间隔（`n*60-19` 容差）；其余 freq 用 `is_due` + `_last_fire[%Y%m%d%H%M]` 同分钟去重；命中后 `run_backup` → 回写 `last_run/last_result` → `_prune_task_backups` 按 keep 清理 |
| `_alert_history_loop` | L409-434 | 60s 采样告警 + 健康分 → metrics 落盘 |
| `_update_loop` | L459-479 | 每小时醒一次，按设置周期调 `updater.check()` 填缓存 |
| `_handle_login` | [L1624-1663](../../src/handlers.py#L1624-L1663) | 登录链：用户名 → 锁定检查（423）→ `verify_admin`（系统库不可达 503）→ `secrets.token_hex(32)` 签发 8h 会话 |
| `_generate_reset_code` / `_handle_reset_password` | L130/L1698 | 找回密码：6 位码打印到服务端终端，10 分钟有效，一次性 |
| `_handle_query` | [L1199-1253](../../src/handlers.py#L1199-L1253) | 只读 SQL：`_query_guard_error` 快速拒写 → 子线程执行 + join 同步等待 → 500 行截断；日志只记前 80 字符 |
| `_native_dialog` | L1158 | 原生文件对话框子线程执行，600s 超时兜底防请求挂死 |
| `p_setup_finish` | [L929-1005](../../src/handlers.py#L929-L1005) | 向导完成：分 lite/full 初始化（full 走 `prepare_full` 建系统库） |
| `_ensure_runtime_scripts` | L1400 | 初始化后生成 start/stop/init 脚本到根目录 |

> 结构体 `OPENFILENAMEW`/`BROWSEINFOW`（L158/L174）：Win32 文件/目录对话框 ctypes 定义，**必须 `restype=c_void_p`** 否则 64 位指针截断。

## 4. 备份引擎（backup_engine.py，全模块级函数）

| 函数 | 位置 | 职责 |
|---|---|---|
| `storage_of(conn_cfg)` | L112-121 | 本地/远程判定：host ∈ {localhost, 127.0.0.1, ::1, …} → local，否则 remote（SSH 直写） |
| `_maybe_tunnel(ctx)` | L92-103 | `ssh_enabled && ssh_host` 时建端口转发隧道，改写 eff 端点为 127.0.0.1:<free_port>，用毕幂等清理 |
| `_cli_args(...)` | [L193-216](../../src/backup_engine.py#L193-L216) | 拼 mysqldump 命令：按 `db_version` 走 `env_probe.find_tool_versioned`；用户 `backup_opts` 追加（连接/输出类参数禁止覆盖） |
| `_prefetch_tables(...)` | L423-446 | information_schema 预查表清单与大小（表级进度分母，排除 4 系统库） |
| `_dump_to_file(...)` | [L450-535](../../src/backup_engine.py#L450-L535) | 本地备份：Popen(stdout=PIPE) + gzip level=6 + 双 daemon 线程（stderr 逐行正则 `for table '...'` 捕获表切换 → 表级进度；stdout 1MB 分块 → 字节进度） |
| `_dump_to_remote(...)` | [L542-655](../../src/backup_engine.py#L542-L655) | 远程备份：先起 `ssh "mkdir -p && cat > path"`（stdin=PIPE），再起 mysqldump，主线程桥接 `gzip.GzipFile(fileobj=ssh_in)`；远端 `gzip -dc \| wc -c` 取实际大小（**必须传远端命令而非裸路径**）；失败判定 dump_rc≠0 ∨ ssh_rc≠0 ∨ size≤0 |
| `_dump_contains_create_db(path)` | L896-903 | 只读前 256KB 匹配 `CREATE DATABASE\|^USE `` `（re.M\|re.I）；读取异常保守返回 True |
| `_ensure_database(...)` | L922-940 | `CREATE DATABASE IF NOT EXISTS` utf8mb4（仅 target_db 指定且文件不含建库语句时） |
| `run_restore / _restore_local / _restore_remote` | L1190/L1028 | 还原：本地 gzip 流式解压写 mysql stdin（stderr 排空线程防管道堵死假死，断管错误与 mysql stderr 拼接保根因）；zip 解临时目录成员取 basename 防穿越后逐成员递归；远程 `gzip -dc` 读流桥接，zip 直接拒绝 |
| `start_backup_task / start_restore_task` | L873/L1300 | 生成 12 位 hex tid，任务快照入内存 `TASKS`，起 daemon worker，进度回调 `lambda **kw: _update_task(tid, **kw)` |
| `get_task(tid)` | — | 返回快照 dict（status/percent/current/message/detail 截 120 条/elapsed） |
| `delete_backup_record(rid)` | — | 删记录 + 白名单校验后删文件 |

## 5. 调度层

### `schedule_store.py`

| 函数 | 位置 | 职责 |
|---|---|---|
| `_default_task()` | L47-54 | 统一任务模型字段全集 |
| `_normalize_task(t)` | L125-168 | 校验归一：freq 白名单、interval 1-23、weekday 0-6、time 补零、keep 1-99、at_once 必填（once） |
| `load_tasks / save_task / delete_task` | — | lite 存 SQLite meta `schedules`（整包 JSON）；full 走 StorageBackend；旧存储一次性迁移并关旧开关防双跑 |
| `is_due(task, now=None)` | [L283-310](../../src/schedule_store.py#L283-L310) | 到点判定：HH:MM 精确字符串比对；weekly `(tm_wday+1)%7 == weekday`；monthly 按日；once ±60s 容差；**hourly 恒 True**（调用方按 last_run 间隔控制） |
| `describe(task)` | L324 | 人性化周期文案（处理「每周周X」重叠） |

### `native_scheduler.py`

| 函数 | 职责 |
|---|---|
| `register(task)` | Windows：`schtasks /create /f /tn MySQLConsole_<id> /tr powershell ... .ps1` + 周期参数；Linux：crontab 行 + marker 幂等；成功后由调用方回写 `native_registered=True` |
| `unregister(task)` | 反注册 + 删脚本；任务不存在也幂等成功 |
| `status(task)` | schtasks /query 或 crontab marker 匹配 |
| `gen_command(task)` | 注册失败时的手动兜底完整命令文本 |

### `native_script.py`

`render()` 按任务渲染自包含脚本（Config 段含 MYSQL_BIN/BACKUP_DIR/连接凭据/KEEP/MIN_SIZE/LOG_FILE/DBS → 探测 mysqldump → 导出 → MIN_SIZE 校验防空包 → gzip → keep 清理 → 日志）。Windows 模板 UTF-8 BOM + CRLF。

### `ssh_tunnel.py`

| 函数 | 职责 |
|---|---|
| `is_ssh_cfg(conn)` | `ssh_enabled && ssh_host` 才启用隧道 |
| `build_tunnel_cmd(...)` | `ssh -N -T -i <key> -L 127.0.0.1:<free>:<bind_host>:<bind_port> <user>@<host>` |
| `wait_tunnel_ready(port)` | 轮询本地端口就绪，8s 超时 |
| `stop_tunnel(tid)` | 注册表 + 幂等清理 |
| `remote_file_size / read_remote_stream` | `gzip -dc <f> \| wc -c` 取大小；起只读流供还原桥接 |
| `probe_remote_env(cfg)` | `uname -s`（MINGW/MSYS/CYGWIN→Git Bash 就绪）/`ver`/PowerShell 版本探测 |

## 6. 数据存储层

### `config_store.py`（模块级函数，无业务类）

| 函数 | 位置 | 职责 |
|---|---|---|
| `_load_key / encrypt / decrypt` | L53-75 | Fernet 密钥管理（`data/.secret.key` 惰性生成）；解密失败容错返回空串 |
| `_hash_password / _verify_password` | L78-93 | PBKDF2-HMAC-SHA256、20 万轮、16 字节盐，存 `salt_hex$hash_hex` |
| `_is_full_mode / _system_db_usable` | L140-173 | 模式判定 + 系统库可达性探测（TTL 缓存 5s/2s + 线程锁） |
| `list_connections / get_connection` | — | 双模式分派；列表出参 `has_password` 去明文（防泄露），单查返回解密明文 |
| `save_connection / delete_connection` | — | 写入（lite 加密密码 + 11 个 SSH/备份可选字段）；full 删除后同步 bootstrap |
| `get_settings / save_settings` | — | `DEFAULT_SETTINGS` 兜底读 + 白名单写；full 模式 `run_mode/sys_db_name` 以本地 meta 为权威 |
| `get_access_token / set_access_token` | L409-418 | 访问令牌明文读 / Fernet 加密写 |
| `verify_admin / set_admin / update_admin_login_fail / get_admin_lock_status` | — | 管理员凭据与失败锁定（计数/locked_until） |
| `switch_to_full_mode(...)` | — | 不可逆切换：备份 config.db → 建系统库 → 迁数据 → 设管理员 → 写 bootstrap → `clear_lite_data()` |
| `prepare_full / prepare_lite / reset_local` | — | 向导引导初始化 / 恢复出厂 |

### `system_db.py`

**模块级函数**：`init_system_db()`（建库建表，幂等补列，未全部成功不置 ready 下次重试——曾因 MySQL 5.7 不支持 TEXT DEFAULT 卡 ssh_key 列）、`is_system_db_ready()`、`import_from_file()`（SQLite→MySQL 全量迁移）、`_task_to_row/_row_to_task`（统一任务模型 ↔ mc_schedule 行，extra JSON + 旧 cron 反解）、`_cron_to_extra()`。

**类 `StorageBackend`**（[L472-829](../../src/system_db.py#L472-L829)）— 只服务全量模式：

| 方法组 | 方法 | 说明 |
|---|---|---|
| 连接 | `_conn()` | 每方法短连接 + `_ensure_conn_cols()` 幂等补列 |
| 设置 | `get_settings / save_settings` | DEFAULT_SETTINGS 兜底、JSON 反序列化、upsert 回读 |
| 连接 | `list/get/save/delete_connection, set_active_conn` | 出参统一映射（password→has_password、is_active→active、username→user） |
| 任务 | `list/get/save/delete_schedule, update_schedule_status` | 经 `_row_to_task` 归一 |
| 历史/日志 | `list/add/delete_history, add_log/list_logs` | 备份历史（限 300 倒序）、操作日志（message 截 2000） |
| 管理员 | `get_admin/set_admin/update_admin_login_fail/update_admin_login_success` | 单行表 id=1 upsert |

### `local_store.py`

`_db()` 上下文管理器（用完必关——Windows WAL 不关锁文件导致 reset 删不掉）；`get/set_meta(_json)`、连接 CRUD（INSERT 后单独 UPDATE SSH 字段防 CREATE 分支遗漏）、`get/save_settings`（值存 JSON 字符串，反序列化交 config_store）、`reset_all()`（删 db/-wal/-shm 三件套）、`clear_lite_data()`（仅留 bootstrap meta）。

### `mysql_client.py`（无业务类，`DbError(Exception)` + 约 30 个模块级函数）

| 函数组 | 代表函数 | 说明 |
|---|---|---|
| 连接 | `connect / test` | utf8mb4、读写超时 30s；`SELECT VERSION()` 测试 |
| 只读查询器 | `run_query` | `QUERY_MAX_ROWS=500` 截断；返回 `{columns, rows, truncated, affected, elapsed}` |
| 查询守卫 | `_query_guard_error` | 拦截可执行注释 `/*!...*/`、WITH 后接 DML、SET GLOBAL/PERSIST、分号多语句；先剥字符串字面量与注释防误判 |
| 监控 | `server_overview / monitor_metrics / monitor_full / health_score / alerts` | 增量轮询基于模块级 `_QPS_CACHE` 两次采样求速率（避免 sleep(1) 阻塞）；评分 0-100 + 四分项 |
| 复制 | `replication_status / _replica_status / _norm_repl` | `SHOW REPLICA STATUS`→`SHOW SLAVE STATUS` 逐条回退，兼容 5.7~8.4 |
| 进程 | `process_list / kill_query / kill_connection` | Info 截 200 字；`KILL QUERY`/`KILL` |
| 用户管理 | `create_user / drop_user / grant_privileges / show_grants` 等 | 标识符白名单正则校验后拼接；`_ALLOWED_PRIVS` 白名单；`_PRESET` 四预设（readonly/dataentry/struct/all） |
| AI 上下文 | `schema_context` | 按表行数降序取 N 张活跃表生成「表(列 类型)」文本，防 prompt 溢出 |

### `metrics.py` / `variable_docs.py`

- `append_alert_sample / append_health_sample`：分钟粒度采样，tmp + `os.replace` 原子写，线程锁，7 天保留，超 1600 点 rollup；
- `alert_history_query / health_history_query`：`?days=1-7` / `?hours=1-168`，近期分钟级、更早小时聚合；
- `ai_report_context(rtype)`：采样汇总为 AI 报告文本；
- `variable_docs.describe(name)`：87 个高频变量中文含义，未收录返回空串不杜撰。

## 7. 平台能力层

| 函数 | 位置 | 职责 |
|---|---|---|
| `env_probe.find_tool(tool)` | [L223-253](../../src/env_probe.py#L223-L253) | 四级探测：用户配置→内置 tools/（SHA256 惰性校验失败则跳过执行）→PATH→常见目录 |
| `env_probe.find_tool_versioned(tool, family)` | L256-276 | 按版本族（5/8）定位，目录名版本排序取最高 |
| `env_probe.env_summary()` | L326-389 | 汇总 Python/pymysql/cryptography/双工具状态（向导数据源） |
| `updater.check()` | [L124-151](../../src/updater.py#L124-L151) | GitHub API → 免验证 SSL 重试 → 本地缓存 → bundled_release.json（offline 标记） |
| `updater.prepare(payload)` | L266-297 | 下载（.part + 大小强校验 + SHA256）→ 解压归一（提升内层 src/ 到顶层）→ 备份当前码到 `data/updates/backup/<ver>/` |
| `updater.build_apply_script()` | L323-412 | 生成独立进程 updater 脚本：`wait_port_free`（90×2s）→ `swap`（删旧拷新，保留 data/.venv/node_modules 等）→ `restart` → update.log |
| `runtime_resolver.resolve()` | [L118-144](../../src/runtime_resolver.py#L118-L144) | 三级解析：bundled → system（真实执行探测 ≥3.10）→ download（嵌入式 3.12.10，三镜像） |
| `runtime_resolver.patch_embedded_ppth()` | L174-193 | 解注 `python3XX._pth` 的 `import site`（幂等）——嵌入式 Python 不读 PYTHONPATH |
| `tools_downloader.start_download(state, lock)` | [L152-225](../../src/tools_downloader.py#L152-L225) | daemon 线程下载双版本客户端，4 源逐试，SHA256 基准校验，只提取 mysqldump/mysql + DLL |
| `pip_bootstrap.bootstrap(python)` | [L85-126](../../src/pip_bootstrap.py#L85-L126) | get-pip 官方→阿里云→清华 simple 解析 wheel→pip 轮子自启动 |
| `security.check_access_token(req)` | L60-67 | `hmac.compare_digest` 常数时间比较 |
| `security.origin_allowed(req)` | L84-99 | Origin/Host 同源校验（CSRF/DNS Rebind） |
| `service_manager.restart_service(name, verify_cb)` | L145-182 | 重启 + 就绪轮询 90s |
| `ai_client.generate_sql / analyze_sql / summarize_report` | L132/L147/L164 | 三场景 prompt 模板（生成强约束只读关键字） |

## 8. 前端关键函数（app.js，3314 行）

| 函数/对象 | 职责 |
|---|---|
| `api()` | 统一请求封装：Bearer `mc_token` + `X-Access-Token` 双头；401 自动跳登录/收集访问令牌；`get/post/put/del` 快捷式 |
| `MCUtils` | 纯函数命名空间（fmtSize/fmtTime/esc），挂 `window.MCUtils` 供测试与未来模块化 |
| `switchPage(page)` | 13 个 section 的页面切换 |
| `loadOverview / monitorLoop / initCharts` | 概览监控 + ECharts 图表工厂（lineOpt/gaugeOpt/donutOpt/hbarOpt） |
| `pollTask(tid)` | 备份/还原异步任务轮询（配合 `showProgressModal` 进度弹窗：百分比 + 当前表 + 消息） |
| `parseGrants(grantsText)` | `SHOW GRANTS` 解析为范围/库/权限结构，供授权编辑弹窗回填 |
| `addQueryTab / runQuery / killQuery` | SQL 查询多页签（每页签独立编辑器/库/结果） |
| `openSetup / runEnvCheck / suProbe / suCheckSysDb` | 三步向导：环境检测→客户端目录→数据库连接 |
| `_dashDownsample` 等 `_dash*` 系列 | dashboard-helpers 纯函数的浏览器端包装 |
| `checkUpdateNow / prepareUpdate / applyUpdate` | 对应 updater.py 的三步 API |
| `applyTheme` | 暗/亮主题切换 |
