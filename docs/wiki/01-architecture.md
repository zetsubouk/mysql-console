# 01 · 项目整体架构

## 1. 设计哲学与选型理由

| 决策 | 选型 | 为什么 |
|---|---|---|
| Web 框架 | **不用**，Python 标准库 `http.server`（ThreadingHTTPServer） | 运行依赖压到 2 个包（pymysql + cryptography），单机便携部署无需 pip 源可用 |
| 前端 | 原生 JS 单文件 + 本地 ECharts | 零构建、零 CDN 依赖，离线环境可用 |
| 存储 | 双模式：SQLite（lite）/ MySQL 系统库（full） | 轻量模式对 MySQL 零依赖即可跑管理台；全量模式解决多端一致性与审计 |
| 凭据加密 | Fernet 对称加密（`cryptography`） | 免密钥管理设施，密钥本地生成落盘 `data/.secret.key` |
| 管理员密码 | PBKDF2-HMAC-SHA256（20 万轮 + 16 字节盐） | 密码不可逆哈希，与可逆的业务凭据（连接密码）分离处理 |
| 备份 | 调用官方 `mysqldump`/`mysql` CLI 子进程 | 保证与被控 MySQL 版本兼容；流式管道支持超大库与实时进度 |
| 打包 | 单仓库 + `src/` 平铺源码 + pyproject | 发布包解压即用，`pip install .` 亦可（`mysql-console` 命令入口） |

## 2. 技术栈总览

- **后端**：Python ≥3.10（规避 3.12+ 语法，兼容 3.10/3.11/3.12），仅 `pymysql` + `cryptography`
- **前端**：原生 JS（非 ES Module）+ ECharts（本地文件）+ jsdom/vitest（仅测试）
- **外部工具**（按需动态探测）：`mysqldump`/`mysql` 客户端、`ssh`、`schtasks`/`crontab`/`systemd`
- **CI**：GitHub Actions，5 个 job（后端矩阵 / 跨平台 / 前端 / E2E / systemd）

## 3. 总体分层架构

```
┌───────────────────────── 浏览器 SPA（src/static/，零构建）─────────────────────────┐
│  index.html(13 个功能页)   app.js(3314 行, 25 区块)   ECharts(本地)   login.html   │
└──────────────────────────────── HTTP :8090 ────────────────────────────────────────┘
                                       │
┌──────────────────────────── 传输层 server.py ──────────────────────────────────────┐
│  ThreadingHTTPServer + Handler（HandlerBase ⊕ BaseHTTPRequestHandler 多继承）       │
│  静态资源 · 认证守卫 · CSRF · JSON 编解码 · 下载流 · PUT/DELETE 内联路由 · main()    │
└───────────┬──────────────────────────────────────────────────────────┬─────────────┘
            ▼                                                          ▼
   routes.py（声明式路由表 + dispatch 反射分发，零依赖）        security.py
                                                              (访问令牌/TLS)
┌─────────────────────────── 业务层 handlers.py (1745 行) ───────────────────────────┐
│  约 60 个 g_*(GET)/p_*(POST)/_handle_*(复合) 处理器                                                 │
│  3 个后台守护线程(备份调度/告警采样/更新检查) · Win/macOS/Linux 原生文件对话框        │
└────┬───────────┬────────────┬──────────────┬──────────────┬─────────────┬─────────┘
     ▼           ▼            ▼              ▼              ▼             ▼
mysql_client  backup_engine schedule_store  config_store  env_probe     updater
 监控/库表/    备份/还原      定时任务双引擎   连接与设置     客户端探测    自更新
 用户/进程    (mysqldump     builtin/native   (Fernet)      (三级链)     (GitHub)
 /SQL查询      流式管道)           │              │
     │           │        native_scheduler  ┌──┴──┐
     ▼           ▼        native_script    ▼     ▼
 被控 MySQL   ssh_tunnel   (schtasks/  local_store  system_db
 (本机或远程)  (SSH 隧道/   cron 适配)  (SQLite     (MySQL 系统库
              远程直写)                 config.db)   _mysql_console)
```

> 交互式架构图（暗色 SVG）见 [docs/architecture.html](../architecture.html)。

## 4. 一次请求的生命周期（GET /api/monitor 为例）

1. **前端发起**：[app.js 的 `api()` 封装](../../src/static/app.js) 自动附带 `Authorization: Bearer <token>` 与 `X-Access-Token` 双头；
2. **接收**：`ThreadingHTTPServer` 每连接一线程，进入 [Handler](../../src/server.py#L49)；
3. **分流**：`do_GET` 判断非 `/api/` 前缀 → 走静态资源服务（`normpath + startswith` 防路径穿越）；
4. **认证守卫**：`_auth_guard()` 三层校验（见 §6）；
5. **路由分发**：`_route_get(path)` → [`routes.dispatch()`](../../src/routes.py#L140-L153)：先查 O(1) 精确表，再按**前缀长度降序**扫前缀表，命中后 `getattr(self, 方法名)` 反射调用；
6. **业务处理**：处理器调用 `mysql_client.monitor_metrics()` 等业务函数；
7. **响应**：`_send_json()`（`default=str` 兜底 datetime 序列化 + `no-store`）；
8. **异常出口**：`mysql_client.DbError` → 400 可读错误；其他异常 → 500。

> POST/PUT/DELETE 在认证后额外经过 `_check_csrf()`（Origin/Host 同源校验）。
>
> 📊 运行期交互的可视化时序图（请求/认证/备份/还原/调度/更新等 11 张）见 [08-sequence-diagrams.md](08-sequence-diagrams.md)。

## 5. 线程模型

| 线程/进程 | 周期 | 职责 | 位置 |
|---|---|---|---|
| HTTP 工作线程 × N | 每连接一线程 | 处理请求 | `ThreadingHTTPServer` |
| `scheduler_loop` | 每 **20 秒** | 内置备份调度：扫描 `engine=builtin 且 enabled` 的任务，`is_due` 判定 + 同分钟去重，触发 `run_backup` 并按 keep 清理旧备份 | [handlers.py:482](../../src/handlers.py#L482-L528) |
| `_alert_history_loop` | 每 **60 秒** | 采样告警 + 健康分 → `metrics` JSON 落盘 | [handlers.py:409](../../src/handlers.py#L409-L434) |
| `_update_loop` | 每 **1 小时**醒一次 | 按 `update_check_interval` 判定到期后调 `updater.check()` | [handlers.py:459](../../src/handlers.py#L459-L479) |
| 备份/还原 worker | 任务期间 | `start_backup_task`/`start_restore_task` 起 daemon 线程执行，进度回调写内存任务快照 | [backup_engine.py:873](../../src/backup_engine.py#L873-L887) |
| 工具下载 daemon | 任务期间 | 瘦版向导下载 MySQL 客户端 | [tools_downloader.py:152](../../src/tools_downloader.py#L152-L225) |
| 自更新进程 | apply 时一次性 | 独立进程 `apply_update.py`：等端口释放 → 原子换码 → 重启服务 | [updater.py:339](../../src/updater.py#L339-L412) |
| OS 调度器 | 外部 | `native` 引擎任务由 schtasks/cron 直接拉起**自包含备份脚本**（不经过本服务进程） | [native_scheduler.py](../../src/native_scheduler.py) |

## 6. 认证与安全模型（三层纵深防御）

```
请求 ──▶ ① 访问令牌门（非回环绑定强制） ──▶ ② Bearer 会话认证 ──▶ ③ CSRF 同源校验 ──▶ 业务
```

| 层 | 机制 | 实现 |
|---|---|---|
| ① 访问令牌门 | 绑定**非回环地址**时强制携带 `X-Access-Token` 头；`hmac.compare_digest` 常数时间比较防时序攻击；令牌来源：环境变量 `MC_ACCESS_TOKEN` > settings（Fernet 加密落库）。缺失时服务**拒绝启动** | [handlers.py:94](../../src/handlers.py#L94-L105)、[security.py:60](../../src/security.py#L60-L67) |
| ② 会话认证 | 未设管理员密码则全免认证；`/api/` 下仅 7 条白名单免登录（login / auth-status / health / request-reset-code / reset-password / security/info / version）；`secrets.token_hex(32)` 签发，内存 `_sessions` 表 8 小时过期；登录失败 5 次锁定（423），系统库不可达回 503 | [handlers.py:71](../../src/handlers.py#L71-L117)、[_handle_login](../../src/handlers.py#L1624-L1663) |
| ③ CSRF 防御 | POST/PUT/DELETE 校验 `Origin` 与 `Host` 同源（防 DNS Rebind / 恶意站点诱导）；无 Origin 头放行 | [security.py:84](../../src/security.py#L84-L99) |

附加安全能力：
- **TLS**：`MC_TLS=1` 启用，自签证书（RSA-2048/3650 天/IP SAN）生成于 `data/tls/`；
- **备份下载白名单**：下载接口路径校验防任意文件读取（[tests/api](../../tests/api/test_api.py) 有专项回归）；
- **SQL 查询器守卫**：前导关键字白名单 + 拦截可执行注释 `/*!...*/`、WITH 后接 DML、SET GLOBAL、分号多语句（[mysql_client.py:81](../../src/mysql_client.py#L81-L199)）；
- **GRANT 标识符白名单**：用户/主机/库名正则校验后拼接（GRANT 无法参数化），密码走 `%s` 绑定。

## 7. 双模式存储架构

```
                    config_store.py（双模式适配器，唯一分派入口）
                          │                        │
              lite（轻量，默认）             full（全量，不可逆切换）
                          ▼                        ▼
              local_store.py               system_db.py → StorageBackend
              SQLite: data/config.db       MySQL 系统库 _mysql_console
              ├ meta(key,value)            ├ mc_config          (KV 设置)
              ├ connections(11+11 列)      ├ mc_connection      (连接, 密码 Fernet)
              └ settings(key,value)        ├ mc_schedule        (定时任务 + extra JSON)
                                           ├ mc_backup_history  (备份历史)
                                           ├ mc_operation_log   (操作日志)
                                           └ mc_admin           (单行管理员 + 锁定)
```

- **lite 模式**：全部数据进 SQLite（WAL 模式），定时任务整包 JSON 存 meta 表 `schedules` 键；对 MySQL 零依赖。
- **full 模式**：`POST /api/switch-to-full-mode` 触发 `switch_to_full_mode()`：备份 config.db → 建系统库（6 表）→ 迁移数据 → 设管理员 → 本地只留 bootstrap；`run_mode/sys_db_name` 以本地 meta 为权威，防系统库残留 `'lite'` 误导。
- **bootstrap 机制**：本地 meta 永久保存"能连系统库的那一条连接"（密码 Fernet 加密），系统库不可达时登录按约定回 503 而**不回退陈旧本地数据**。
- **循环依赖化解**：`config_store ⇄ system_db` 互相依赖（加密函数 vs 存储后端），靠**函数内延迟 import** 打破。

## 8. 备份/还原执行架构（核心能力）

```
                    ┌────────── run_backup(conn_cfg, storage_cfg, ...) ──────────┐
                    │                                                            │
        storage_of(host) 判定存储位置                          _maybe_tunnel(ssh_enabled?)
        localhost/127.0.0.1/::1 → local                       │ 是：ssh -N -T -L 端口转发
        其它 → remote                                          ▼
        │            │                                  mysqldump 连接 127.0.0.1:<free_port>
        ▼            ▼
  _dump_to_file  _dump_to_remote
  gzip 落本地盘   ssh "mkdir -p && cat > path"（stdin=PIPE）
                 gzip.GzipFile(fileobj=ssh_in) —— 客户端内存压缩，全程不落本地盘
        │            │
        └────┬───────┘
             ▼
  双 stderr 排空线程：mysqldump --verbose 表切换事件 → 表级进度；[ERROR] 行收集
  主循环 1MB 分块读 stdout → 字节进度（分母=information_schema 预查表大小总和）
             ▼
  start_backup_task → 12 位 hex task_id → 内存 TASKS dict → 前端轮询 GET /api/task/<id>
```

关键设计点：

| 设计 | 说明 | 位置 |
|---|---|---|
| 双维度进度 | 字节级（百分比）+ 表级（`current=表名`，不参与百分比） | [backup_engine.py:450-535](../../src/backup_engine.py#L450-L535) |
| 还原进度分母 | 读 `.gz` 尾部 4 字节 **ISIZE 字段**（解压后大小 mod 2³²），避免"压缩包 149MB 播到 100% 实际才还原 1/6"的失真 | [backup_engine.py:906-919](../../src/backup_engine.py#L906-L919) |
| 自动补建库 | 只读备份文件**前 256KB** 匹配 `CREATE DATABASE|^USE `` `；不含建库语句且指定 target_db 时 `CREATE DATABASE IF NOT EXISTS`（utf8mb4） | [backup_engine.py:896-940](../../src/backup_engine.py#L896-L940) |
| 版本族匹配 | 内置多版本客户端时按连接声明的 `db_version`（5.7/8.x）自动选 mysqldump，避免 8.x 工具导 5.7 数据的兼容问题 | [env_probe.find_tool_versioned](../../src/env_probe.py#L256-L276) |
| 多库打包 | zip 用 `ZIP_STORED`（成员已是 .gz，二次压缩无收益），打包后删散件 | [backup_engine.py:811](../../src/backup_engine.py#L811-L812) |
| 远端 Windows | `probe_remote_env` 用 `uname -s`/`ver` 探测 Git Bash 就绪，否则抛引导性错误 | [ssh_tunnel.py:132-191](../../src/ssh_tunnel.py#L132-L191) |

## 9. 定时备份双引擎

| 维度 | builtin（内置引擎） | native（系统计划任务） |
|---|---|---|
| 执行者 | Web 进程内守护线程（20s 轮询） | OS 调度器（Windows schtasks / Linux crontab） |
| 依赖 | 服务进程必须存活 | 注册一次，进程死了也跑 |
| 执行体 | `run_backup`（与手动备份同路径） | `native_script` 生成的**自包含脚本**：Windows `.ps1`（UTF-8 BOM+CRLF）/ Linux `.sh`（chmod 700），内部自带 mysqldump 探测/压缩/keep 清理/日志，**不经过 Python** |
| 注册命令 | — | `schtasks /create /tn MySQLConsole_<id> /tr "powershell ... backup_<id>.ps1"` 或 crontab 行 `分 时 日 月 周 /bin/bash scripts/backup_<id>.sh #mysqlconsole:<id>`（marker 幂等） |
| 一致性 | — | 引擎切换 native→builtin 自动反注册；DELETE 任务先反注册（[server.py:206-252](../../src/server.py#L183-L262)） |

到点匹配核心：[`schedule_store.is_due()`](../../src/schedule_store.py#L283-L310) —— **tm_wday 陷阱**：Python `tm_wday` 0=周一，业务约定 0=周日，必须 `(tm_wday + 1) % 7` 平移；hourly 恒 True，由调用方按 `last_run` 间隔（含 19 分钟容差）判定。

## 10. 部署形态与运行时解析

**发布产物 4 包矩阵**（`scripts/build_release.py`）：

| 包 | 大小 | 含 MySQL 客户端 | 含 Python 运行时 |
|---|---|---|---|
| standard-win64 / standard-linux | ~30MB | 是（5.7 + 8.0 双版本） | 否 |
| slim-win64 / slim-linux | ~600K | 否（向导可下载/跳过） | 否 |
| standard-win64 + `--with-runtime` | 更大 | 是 | 是（嵌入式 Python 3.12.10 + 预装依赖，全程离线） |

**三级运行时解析**（无 Python 也能装，策略在 Python 与 bat 中**重复实现并必须同步**）：

```
① 内置 runtime/python/  →  ② 系统 Python 实测执行 ≥3.10（防商店占位符）  →  ③ 下载嵌入式
   （完整包/历史下载）        py -3 / python / python3                       python.org → 华为云 → npmmirror
```

**MySQL 客户端四级探测链**：用户配置 → 内置 `tools/`（SHA256 惰性校验）→ PATH → 常见安装目录（phpStudy/xampp/wamp64/choco…）。

## 11. 设计取舍与已知限制（改动前必读）

| 项 | 现状 | 影响 |
|---|---|---|
| 会话/任务快照纯内存 | `_sessions`、`backup_engine.TASKS` 重启即失 | 重启后需重新登录；进行中备份任务状态丢失（备份文件不受影响） |
| `_task_lock` 未生效 | 声明后从未 acquire，属死代码 | 并发备份/还原**没有**互斥限制 |
| 远程备份历史 size=0 | `_dump_to_remote` 已返回远程大小但未累计入 record | 历史列表远程记录大小显示 0 |
| PUT/DELETE 未进路由表 | 仍为 server.py 内联 if-elif | 新增 PUT/DELETE 接口需改 server.py 而非 routes.py |
| bat 脚本硬约束 | 纯 ASCII + CRLF，块内 echo 禁半角圆括号，shift 后禁用 `%~dp0` | 违反必炸（详见 [07 文档避坑清单](07-testing-and-ci.md#五避坑清单)） |
| 语法基线 3.10 | f-string 内嵌同类引号是 3.12+（PEP 701）语法，**严禁** | 新代码需在 3.10 语义下编写 |
| 定时脚本内嵌明文密码 | native_script 生成的脚本含明文 DB 密码 | 单机工具取舍；脚本权限 700，需注意宿主机安全 |
| 三级策略多处同步 | runtime_resolver.py / _resolve_python.bat / install.bat 三处重复实现解析顺序 | 改顺序必须三处一起改 |
