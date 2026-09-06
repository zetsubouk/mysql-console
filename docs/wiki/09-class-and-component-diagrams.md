# 09 · 类图与组件图（静态结构视图）

> **阅读说明**：本页为 [08-sequence-diagrams.md](08-sequence-diagrams.md) 时序图对应的**静态结构视图**，共 5 张图。
> - **选型理由**：类图用 Mermaid `classDiagram`（代码级结构，展示真实类/方法名，备选 PlantUML class）；组件图与部署图用 Mermaid `flowchart` 分层子图（备选 Mermaid C4——因 GitHub 渲染兼容性欠佳而未选）。全部成员名经源码 `grep` 核验，非推断。
> - **渲染**：GitHub / VS Code / Typora 原生支持；导出可用 `mmdc -i 09-class-and-component-diagrams.md -o out.svg`。
> - **建模约定**：Python 无类的模块（本项目的多数模块）按 UML «module» 构造型建模，模块级函数列为成员；`-` 前缀表示模块内部函数。

## 0. 与时序图的对应关系

| 静态视图 | 支撑的时序图（08 文档） |
|---|---|
| §1 组件图 | 全部时序图的参与者拓扑总览 |
| §2 类图·HTTP 服务层 | 图 1（请求生命周期）、图 2（登录） |
| §3 类图·数据存储层 | 图 2（verify_admin/锁定）、图 3（历史落库）、图 9/10（向导与模式切换） |
| §4 类图·备份与调度域 | 图 3（备份）、图 4（还原）、图 5（builtin）、图 6（native）、图 7（隧道） |
| §5 部署图 | 图 3/7（执行位置）、图 6（OS 到点拉起）、图 8（apply 换码与重启后的进程关系） |

## 1. 组件图：分层组件视图

```mermaid
flowchart TD
    subgraph FE["前端 SPA（src/static，零构建）"]
        APP["app.js<br/>api() 双认证头 / pollTask 轮询 / ECharts"]
        LOGIN["login.html 登录与找回密码"]
    end

    subgraph CORE["传输与路由层"]
        SRV["server.py<br/>ThreadingHTTPServer + Handler<br/>PUT/DELETE 内联路由"]
        RT["routes.py<br/>39 GET + 39 POST 声明式路由表"]
        SEC["security.py<br/>访问令牌 / CSRF / TLS"]
        PTH["paths.py / version.py"]
    end

    subgraph BIZ["业务层 handlers.py（1745 行）"]
        HB["HandlerBase 约 60 个处理器<br/>g_* / p_* / _handle_*"]
        THR["3 个守护线程<br/>调度 20s / 告警 60s / 更新 1h"]
        DLG["原生文件对话框<br/>Win32 / osascript / zenity"]
    end

    subgraph BK["备份与调度域"]
        BE["backup_engine.py<br/>流式管道 + 双进度 + 任务快照"]
        SST["schedule_store.py<br/>任务模型 + is_due"]
        NSD["native_scheduler.py<br/>schtasks / crontab 注册"]
        NSC["native_script.py<br/>自包含脚本生成"]
        STU["ssh_tunnel.py<br/>隧道 / 远程读写流"]
        CLB["cli_backup.py / cli_init.py"]
    end

    subgraph DATA["数据存储层"]
        CFS["config_store.py<br/>双模式适配 + Fernet + PBKDF2"]
        MYC["mysql_client.py<br/>监控 / 库表 / 用户 / 只读查询器"]
        LST["local_store.py<br/>SQLite data/config.db"]
        SDB["system_db.py<br/>StorageBackend 6 张 mc_* 表"]
        MET["metrics.py 采样落盘"]
        VD["variable_docs.py 离线词典"]
    end

    subgraph PLAT["平台能力层"]
        EPB["env_probe.py 客户端四级探测"]
        UPD["updater.py 自更新"]
        RTR["runtime_resolver.py 三级运行时"]
        TDL["tools_downloader.py 客户端下载"]
        PPB["pip_bootstrap.py pip 引导"]
        SVM["service_manager.py"]
        SYR["sys_resources.py"]
        AIC["ai_client.py"]
    end

    subgraph EXT["外部系统"]
        MYSQL[("被控 MySQL :3306<br/>本机或远程")]
        SYSDB[("MySQL 系统库<br/>_mysql_console")]
        DUMP["mysqldump / mysql CLI"]
        SSHD["ssh / schtasks / crontab"]
        GH["GitHub Releases API"]
        MIR["官方源与国内镜像"]
        AI["OpenAI 兼容 API"]
    end

    APP -->|"HTTP :8090（Bearer + X-Access-Token）"| SRV
    LOGIN --> SRV
    SRV --> RT
    SRV --> SEC
    SRV --> HB
    HB --> RT
    HB -->|"查询/管理"| MYC
    HB -->|"连接与设置"| CFS
    HB -->|"发起备份/还原"| BE
    HB -->|"任务增删与到点扫描"| SST
    HB -->|"注册/反注册"| NSD
    HB -->|"采样读写"| MET
    HB --> DLG
    THR --> SST
    THR --> MET
    CLB -->|"定时任务 CLI"| BE
    BE -->|"定位客户端"| EPB
    BE --> MYC
    BE --> STU
    BE -->|"读取任务/keep"| SST
    BE -->|"子进程管道"| DUMP
    STU --> SSHD
    NSD --> SSHD
    NSC -.->|"OS 到点拉起"| SSHD
    SST -->|"lite 存储"| LST
    SST -->|"full 存储"| SDB
    CFS --> LST
    CFS --> SDB
    MYC --> MYSQL
    MYC --> VD
    SDB --> SYSDB
    SVM -.->|"OS 服务控制"| MYSQL
    UPD --> GH
    TDL --> MIR
    RTR --> MIR
    PPB --> MIR
    AIC --> AI
```

> 阅读要点：`handlers.py` 是唯一触达几乎所有模块的**枢纽**（图 1 时序中"业务层"一步的展开）；`config_store ⇄ system_db` 的双向依赖在图中表现为两条边（延迟 import 化解，见 [04-dependencies.md §2](04-dependencies.md#2-循环依赖与化解手法)）。

## 2. 类图：HTTP 服务层（支撑时序图 1、2）

```mermaid
classDiagram
    direction TB
    class BaseHTTPRequestHandler {
        <<stdlib http.server>>
        +do_GET()
        +do_POST()
        +do_PUT()
        +do_DELETE()
    }
    class ThreadingHTTPServer {
        <<stdlib http.server>>
        +serve_forever()
    }
    class Handler {
        <<server.py 传输层>>
        +log_message()
        +handle_error()
        +_send_json(data, status)
        +_send_error(msg, status)
        +_read_body() dict
        +_auth_guard() bool
        +_serve_static(path)
        +_serve_download(path)
        +do_HEAD()
        +do_GET()
        +do_POST()
        +do_PUT()
        +do_DELETE()
    }
    class HandlerBase {
        <<handlers.py 业务层>>
        -_sessions: dict
        -_update_cache: dict
        +_route_get(path) bool
        +_route_post(path) bool
        +_current_user() str
        +_log_op(action, ok, detail)
        +g_health()
        +g_monitor()
        +g_task(path)
        +g_user_detail_or_grants(path)
        +p_backup(body)
        +p_restore(body)
        +p_setup_finish(body)
        +_handle_login(body)
        +_handle_query(body)
        +_handle_user_update(path, body)
        +_handle_ai_config(body)
        +其余约 50 个 g/p/handler 处理器
    }
    class handlers_funcs {
        <<handlers.py 模块级函数>>
        +scheduler_loop()
        +_update_loop()
        +_alert_history_loop()
        +_check_access_token(handler) bool
        +_check_auth(handler) bool
        +_check_csrf(handler, method) bool
        +_is_auth_required(path) bool
        +_get_conn() dict
        +_set_active_conn(cid)
        +_generate_reset_code() str
        +_clear_expired_sessions()
        +_prune_task_backups(task)
        +_posix_open_file(title, start_dir)
        +_posix_open_dir(title, start_dir)
    }
    class routes_module {
        <<routes.py 声明式路由>>
        +GET_ROUTES: list
        +POST_ROUTES: list
        -_GET_EXACT: dict
        -_GET_PREFIX: list
        +dispatch(self, method, path, body) bool
        +_invoke(self, fn_spec, argkind, path, body) bool
        +_exact(spec)
        +_prefix(spec)
    }
    class security_module {
        <<security.py 安全加固>>
        +target_host() str
        +is_loopback(host) bool
        +bind_port() int
        +access_token_required() bool
        +effective_access_token() str
        +check_access_token(incoming) bool
        +origin_allowed(request_host, origin, allow_null) bool
        +tls_enabled() bool
        +ensure_cert(data_dir, host)
        +wrap_socket(raw_sock, data_dir, host)
        +_gen_self_signed(cert_path, key_path, host)
    }
    class OPENFILENAMEW {
        <<ctypes Win32 结构体>>
    }
    class BROWSEINFOW {
        <<ctypes Win32 结构体>>
    }

    BaseHTTPRequestHandler <|-- Handler
    HandlerBase <|-- Handler
    ThreadingHTTPServer o-- Handler : 每连接一线程实例化
    Handler ..> routes_module : _route_get 与 _route_post 分发
    routes_module ..> HandlerBase : getattr 反射取处理器
    Handler ..> handlers_funcs : 认证守卫调用
    Handler ..> security_module : 令牌比对与 CSRF 与 TLS
    handlers_funcs ..> OPENFILENAMEW : Win32 对话框
    handlers_funcs ..> BROWSEINFOW : Win32 目录对话框
```

> 建模要点：`_check_auth`/`scheduler_loop` 等是 handlers.py 的**模块级函数**（接收 handler 参数），不是 HandlerBase 方法——图中以 `handlers_funcs` 单独建模，避免误读。

## 3. 类图：数据存储层（支撑时序图 2、3、9、10）

```mermaid
classDiagram
    direction TB
    class Exception {
        <<stdlib>>
    }
    class SystemDbUnavailable {
        <<config_store.py>>
    }
    class DbError {
        <<mysql_client.py>>
    }
    class config_store {
        <<module·双模式适配器>>
        +DEFAULT_SETTINGS: dict
        +encrypt(plain) str
        +decrypt(token) str
        -_hash_password(password, salt) str
        -_verify_password(password, stored) bool
        -_load_key()
        -_is_full_mode() bool
        -_system_db_usable() bool
        -_get_backend()
        -_migrate_legacy_json()
        -_get_bootstrap_conn_cfg()
        +list_connections() list
        +get_connection(cid) dict
        +save_connection(payload, cid)
        +delete_connection(cid)
        +get_settings() dict
        +save_settings(patch) dict
        +get_access_token() str
        +set_access_token(token)
        +verify_admin(password) bool
        +set_admin(username, password)
        +get_admin_lock_status() dict
        +update_admin_login_fail(count, locked_until)
        +switch_to_full_mode(db, user, pw)
        +prepare_full(db, cfg)
        +prepare_lite()
        +reset_local()
        +add_operation_log(level, message, operator)
    }
    class system_db {
        <<module·系统库管理>>
        +init_system_db(cfg, db)
        +is_system_db_ready(cfg, db, timeout) bool
        +import_from_file(cfg, db, source)
        +get_sys_conn(cfg, db)
        -_ensure_conn_cols(conn)
        -_cron_to_extra(cron_expr) dict
        -_task_to_row(t) dict
        -_row_to_task(r) dict
    }
    class StorageBackend {
        <<system_db.py 全量模式后端>>
        -conn_cfg: dict
        -db_name: str
        +_conn()
        +get_settings() dict
        +save_settings(patch)
        +list_connections() list
        +get_connection(cid) dict
        +save_connection(payload, cid)
        +delete_connection(cid)
        +set_active_conn(cid)
        +list_schedules() list
        +save_schedule(payload, sid)
        +delete_schedule(sid)
        +update_schedule_status(sid, result)
        +list_history() list
        +add_history(record)
        +delete_history(rid)
        +add_log(level, msg, operator)
        +list_logs(limit) list
        +get_admin() dict
        +set_admin(user, pw_hash)
    }
    class local_store {
        <<module·SQLite 轻量存储>>
        +get_meta(key, default)
        +set_meta(key, value)
        +get_meta_json(key, default)
        +set_meta_json(key, obj)
        +list_connections() list
        +get_connection(cid) dict
        +save_connection(payload, cid)
        +delete_connection(cid)
        +set_active_conn(cid)
        +get_settings() dict
        +save_settings(patch)
        +reset_all()
        +clear_lite_data()
        -_db(commit) contextmanager
        -_migrate(c)
    }
    class mysql_client {
        <<module·被控库查询>>
        +connect(cfg, timeout, database)
        +test(cfg) str
        +run_query(conn, sql, max_rows) dict
        -_query_guard_error(sql)
        -_query_leading_keyword(sql) str
        -_strip_quoted_comments(sql) str
        +server_overview(conn)
        +monitor_metrics(conn, key)
        +monitor_full(conn, key)
        +health_score(conn)
        +replication_status(conn)
        +alerts(conn, max_conn, max_slow, max_running)
        +process_list(conn)
        +kill_query(conn, pid)
        +create_user(conn, user, host, password)
        +grant_privileges(conn, user, host, db, privileges)
        +show_grants(conn, user, host)
        +schema_context(conn, db, max_tables) str
    }
    class variable_docs {
        <<module·离线词典>>
        +describe(name) str
        +known_dict() dict
    }
    class PyMySQL {
        <<第三方依赖>>
    }
    class Fernet {
        <<cryptography>>
    }
    class SQLite3 {
        <<stdlib sqlite3>>
    }

    Exception <|-- SystemDbUnavailable
    Exception <|-- DbError
    system_db *-- StorageBackend : 定义于同模块
    config_store ..> local_store : lite 模式
    config_store ..> StorageBackend : full 模式延迟 import
    system_db ..> config_store : encrypt 与 decrypt
    config_store ..> Fernet : 密钥落盘 data/.secret.key
    mysql_client ..> PyMySQL
    StorageBackend ..> PyMySQL
    local_store ..> SQLite3 : WAL 模式
    mysql_client ..> variable_docs : 变量中文含义
```

## 4. 类图：备份与调度域（支撑时序图 3、4、5、6、7）

```mermaid
classDiagram
    direction TB
    class backup_engine {
        <<module·核心引擎>>
        +TASKS: dict
        -_task_lock: Lock
        +run_backup(conn_cfg, dbs, backup_dir, gzip_) 
        +run_restore(conn_cfg, target_db, file_path, storage)
        +start_backup_task(conn_cfg, dbs) str
        +start_restore_task(conn_cfg, target_db, file_path) str
        +get_task(tid) dict
        +delete_backup_record(record_id)
        +list_backups() list
        +list_backup_files(limit) list
        +resolve_backup_file(raw_path) str
        +list_remote_files(cfg, remote_dir) list
        +validate_extra_opts(kind, tokens)
        -_run_backup(storage_cfg, conn_cfg, dbs)
        -_run_restore(storage_cfg, conn_cfg, target_db)
        -_maybe_tunnel(conn_cfg)
        -_is_local_host(host) bool
        -storage_of(conn_cfg) str
        -_cli_args(conn_cfg, tool) list
        -_prefetch_tables(conn_cfg, dbs) list
        -_dump_to_file(conn_cfg, dbs, out_path, gzip_, opts, tables, cb)
        -_dump_to_remote(storage_cfg, db_endpoint, db, remote_path)
        -_remote_backup(storage_cfg, db_endpoint, dbs)
        -_remote_restore(storage_cfg, conn_cfg, target_db)
        -_dump_contains_create_db(path) bool
        -_gz_uncompressed_size(path) int
        -_ensure_database(conn_cfg, db_name)
        -_new_task(kind, desc) str
        -_update_task(tid)
        -_save_history(record)
    }
    class schedule_store {
        <<module·任务模型与到点匹配>>
        +list_tasks() list
        +get_task(tid) dict
        +save_task(payload, tid)
        +delete_task(tid) bool
        +set_enabled(tid, enabled)
        +update_run_status(tid, result)
        +set_native_registered(tid, registered)
        +is_due(task, now, check_enabled) bool
        +describe(task) str
        -_default_task() dict
        -_normalize_task(payload, exist) dict
        -_load() list
        -_save(tasks)
        -_migrate_legacy()
    }
    class native_scheduler {
        <<module·OS 计划任务适配>>
        +register(task) dict
        +unregister(task) dict
        +status(task) dict
        +gen_command(task) str
        +env_info() dict
        -_generate_script(task)
        -_script_path(task)
        -_register_linux(task)
        -_cron_line(task) str
        -_unregister_linux(task)
        -_status_windows(task)
        -_status_linux(task)
        -_win_sch_args(task)
    }
    class native_script {
        <<module·自包含脚本生成>>
        +build(task, conn_cfg, settings, script_dir, os_type)
        -_render(template, mapping) str
        -_ps1_mapping(task, conn_cfg, settings, backup_dir, all_mode)
        -_sh_mapping(task, conn_cfg, settings, backup_dir, all_mode)
        -_write_ps1(content, path)
        -_write_sh(content, path)
        -_ps1_squote(s) str
        -_sh_squote(s) str
    }
    class ssh_tunnel {
        <<module·SSH 辅助层>>
        +is_ssh_cfg(cfg) bool
        +ssh_available() bool
        +pick_free_port(preferred) int
        +ssh_prefix(cfg) list
        +build_tunnel_cmd(cfg, local_port) list
        +start_tunnel(cfg) dict
        +stop_tunnel(info)
        +ensure_tunnel_stopped()
        +remote_file_size(cfg, remote_cmd) int
        +remote_file_size_by_path(cfg, path) int
        +read_remote_stream(cfg, remote_cmd)
        +ssh_run(cfg, cmd, timeout)
        +probe_remote_env(cfg) dict
    }
    class cli_backup {
        <<module·定时 CLI>>
        +run_task(tid) int
        +main()
        -_prune(task)
    }
    class cli_init {
        <<module·恢复出厂 CLI>>
        +check_info() dict
        +print_summary(info)
        +do_init(info)
        +main()
    }
    class mysqldump {
        <<外部 CLI>>
    }
    class mysql_cli {
        <<外部 CLI>>
    }
    class ssh_bin {
        <<外部 CLI·仅密钥认证>>
    }
    class os_scheduler {
        <<schtasks / crontab>>
    }

    backup_engine ..> ssh_tunnel : 隧道与远程读写
    backup_engine ..> mysqldump : 流式管道
    backup_engine ..> mysql_cli : 还原 stdin
    native_scheduler ..> native_script : 生成脚本并落盘
    native_scheduler ..> os_scheduler : 注册与反注册
    os_scheduler ..> native_script : 到点拉起已生成脚本
    cli_backup ..> backup_engine : run_task 复用引擎
    cli_init ..> backup_engine : 清理范围不含业务库
```

> 建模要点：`_task_lock` 在源码中声明但从未 acquire（已知问题，见 [01-architecture.md §11](01-architecture.md)），图中如实保留该成员；`os_scheduler → native_script` 的虚线表达"注册一次、OS 到点直接拉起脚本、不经 Python"的时序图 6 关键语义。

## 5. 部署图：运行期拓扑（支撑时序图 3、6、7、8）

```mermaid
flowchart LR
    subgraph CLIENT["操作员工作站"]
        BR["浏览器<br/>SPA + login"]
    end

    subgraph HOST["部署机（无需安装 MySQL）"]
        subgraph SVC["服务进程 :8090（MC_HOST / MC_PORT / MC_TLS）"]
            HTTP["ThreadingHTTPServer"]
            T1["scheduler_loop 20s"]
            T2["告警采样 60s"]
            T3["更新检查 1h"]
        end
        subgraph FS["文件系统"]
            DD[("data/<br/>config.db · .secret.key<br/>backups · updates · tls")]
            RT[("runtime/python<br/>三级解析运行时")]
            TL[("tools/<br/>mysqldump mysql 5.7 与 8.0")]
            SCRIPTS[("scripts/backup_id.ps1 .sh<br/>自包含定时脚本")]
        end
        OS["OS 计划任务<br/>schtasks / crontab"]
        VENV[".venv 隔离环境"]
    end

    subgraph REMOTE["被管基础设施"]
        DB[("MySQL Server :3306<br/>本机或远程")]
        JUMP["SSH 跳板机（可选）<br/>remote_backup_dir"]
    end

    subgraph CLOUD["外部服务"]
        GH["GitHub Releases"]
        MIR["官方源与镜像<br/>python.org 阿里云 清华"]
    end

    BR -->|"HTTP(S) :8090"| HTTP
    T1 -.->|"读任务/写历史"| DD
    T2 -.->|"采样"| DD
    T3 -->|"HTTPS"| GH
    VENV -.->|"承载"| SVC
    RT -.->|"三级解析①"| SVC
    OS -->|"到点拉起"| SCRIPTS
    SCRIPTS -->|"直连 mysqldump"| DB
    HTTP -->|"PyMySQL :3306"| DB
    HTTP -->|"ssh -L 隧道 / 远程直写"| JUMP
    JUMP --> DB
    UPD2["apply_update.py 独立进程"] -.->|"等端口释放后换码并重启"| SVC
    SVC -.->|"prepare 下载（大小与 SHA256 校验）"| GH
    RT -.->|"下载嵌入式 Python"| MIR
    TL -.->|"向导下载（4 源逐试）"| MIR
```

> 阅读要点：`OS 计划任务 → 自包含脚本 → MySQL` 是一条**完全绕开服务进程**的执行链（时序图 6 的关键结论：服务停了定时备份照跑）；`apply_update.py` 与服务进程是**父子接力**关系——主进程先退出、独立进程等端口释放后换码再拉起新服务（时序图 8）。

## 6. 视图清单与维护约定

| 视图 | 类型 | 图数 | 对应文档 |
|---|---|---|---|
| §1 组件图 | Mermaid flowchart（分层子图） | 1 | [02-modules.md](02-modules.md)、[04-dependencies.md](04-dependencies.md) |
| §2 HTTP 服务层类图 | Mermaid classDiagram | 1 | [03-classes-and-functions.md §1-3](03-classes-and-functions.md) |
| §3 数据存储层类图 | Mermaid classDiagram | 1 | [03-classes-and-functions.md §6](03-classes-and-functions.md) |
| §4 备份调度域类图 | Mermaid classDiagram | 1 | [03-classes-and-functions.md §4-5](03-classes-and-functions.md) |
| §5 部署图 | Mermaid flowchart | 1 | [05-running.md](05-running.md)、[08-sequence-diagrams.md](08-sequence-diagrams.md) |

> **维护约定**：类图成员来自源码 `grep "^def \|^class "` 实测（2026-09-06，v3.8.1）。重构涉及模块级函数改名/增删时，请同步更新 §2-§4 对应类图；新增跨模块调用建议同步 §1 组件图的边标签。
