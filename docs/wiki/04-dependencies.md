# 04 · 依赖关系

## 1. 模块依赖图（同项目 import，AST 实测）

```
                        ┌───────── 零依赖层 ─────────┐
                        │ version  paths  routes  variable_docs │
                        └───────┬───────────────────-┘
                                │
                    local_store ──► paths
                                │
              metrics ──────────┤（仅依赖 paths.DATA_DIR，独立）
                                │
                    config_store ──► local_store
                       │  ▲                ┌── 延迟 import system_db ──┐
                       │  └── system_db ───┘（encrypt/decrypt 为顶层依赖，
                       │        │            DEFAULT_SETTINGS 延迟）
                       │        └── 延迟 import local_store（import_from_file）
                       │
              security ──► config_store（读访问令牌密文）
                       │
              ai_client ──► config_store（延迟，解密 api_key）
                       │
              mysql_client ──► variable_docs（延迟 describe）
                       │
   ┌───────────────────┴────────────────────────────────────────┐
   │                      备份与调度层                            │
   │ backup_engine ──► mysql_client · config_store · schedule_store │
   │        │            · ssh_tunnel · env_probe · local_store/system_db │
   │ schedule_store ──► local_store · config_store（延迟 backend）  │
   │ native_scheduler / native_script / ssh_tunnel / cli_backup / cli_init │
   └───────────────────┬────────────────────────────────────────────┘
                       │
              handlers ──► routes · security · paths · config_store · local_store
                │           · mysql_client · backup_engine · schedule_store
                │           · native_scheduler · env_probe · ssh_tunnel · metrics
                │           延迟：updater · ai_client · sys_resources · system_db
                │                 · service_manager · tools_downloader · version
                │        （handlers 刻意不 import server —— 防循环依赖）
                       │
              server ──► paths · security · handlers
                          · mysql_client(DbError) · config_store · backup_engine
                          · schedule_store · native_scheduler（后四者服务 PUT/DELETE 内联路由）
```

**依赖方向总结**：单向分层 `server → handlers → routes`；数据层 `paths ← local_store ← config_store ⇄ system_db`；`mysql_client → variable_docs`；`metrics` 完全独立。

## 2. 循环依赖与化解手法

| 环 | 化解方式 |
|---|---|
| `config_store ⇄ system_db` | system_db 顶层需要 config_store 的 `encrypt/decrypt`；config_store 在**函数内延迟** import system_db 的 `StorageBackend/init_system_db` |
| `handlers ↔ server` | handlers **永不** import server（模块头注释明确约定）；server 多继承 handlers.HandlerBase 单向依赖 |
| 重模块启动开销 | `updater/ai_client/system_db/service_manager/tools_downloader` 只在对应 handler 函数内 import |

## 3. 外部依赖清单

### 3.1 运行时依赖（最小集）

| 依赖 | 版本 | 用途 | 引入点 |
|---|---|---|---|
| Python | ≥3.10（规避 3.12+ 语法） | 运行时 | — |
| pymysql | `>=1.1,<2` | 被控 MySQL 连接与查询、系统库 StorageBackend | mysql_client / system_db |
| cryptography | `>=42` | Fernet 加密（连接密码/AI key/访问令牌）、PBKDF2 | config_store（security 延迟生成证书） |

> 注意：`pyproject.toml` 的 `py-modules` 清单（22 个）**未包含** `ai_client / native_script / pip_bootstrap / runtime_resolver / ssh_tunnel` 这 5 个实际存在的模块，而 handlers.py 顶层 import 了 ssh_tunnel——`pip install .` 路径可能缺模块（源码直跑与发布包不受影响）。属于需验证的潜在缺口，标注供维护者核对。

### 3.2 外部工具（动态探测，缺什么向导提示什么）

| 工具 | 用途 | 探测/降级 |
|---|---|---|
| mysqldump / mysql | 备份/还原 | 四级链：用户配置→内置 tools/（sha256 惰性校验）→PATH→常见目录；可向导下载双版本 |
| ssh | SSH 隧道与远程备份直写 | PATH；仅密钥认证；远端 Windows 需 Git Bash（`probe_remote_env` 探测） |
| schtasks / crontab / systemctl | native 定时任务注册 | 按平台选择；失败给手动兜底命令 |
| gzip / zip 相关 | 压缩 | Python 内置 gzip/zipfile 实现，不依赖外部命令 |
| lsof（stop.sh）/ netstat（stop.bat） | 端口清理 | 平台脚本内置 |
| psutil（可选） | IOPS/网络吞吐 | 缺失返回 None，前端隐藏图表 |

### 3.3 外部服务

| 服务 | 用途 | 失败降级 |
|---|---|---|
| GitHub Releases API | 自更新检查/下载 | 免验证 SSL 重试 → 本地缓存 → 随包 bundled_release.json（offline） |
| MySQL 官方/阿里云/清华/cdn | MySQL 客户端下载 | 4 源逐试 + official_sha256.json 带外校验 |
| python.org/华为云/npmmirror | 嵌入式 Python 下载 | 3 源逐试 + 超时保护 |
| get-pip（官方/阿里/清华） | pip 在线引导 | 3 通道逐试 |
| OpenAI 兼容 API | AI 辅助（可选） | 未配置则功能入口隐藏；异常转 400 可读错误 |

### 3.4 开发/测试依赖

| 依赖 | 用途 |
|---|---|
| Node.js 20 + jsdom ^26 + vitest 3.x | 前端回归（`npm test`，devDependencies 固化） |
| setuptools ≥68 | `pip install .` 打包 |

## 4. 数据流向依赖（非 import 的运行期依赖）

```
被控 MySQL ──PyMySQL──► mysql_client（监控/查询/管理）
被控 MySQL ◄─子进程管道─► mysqldump/mysql CLI（备份/还原）
SSH 宿主机 ◄─ssh 信道──── ssh_tunnel（隧道转发 / 远程直写备份）
OS 调度器 ──拉起──────► native_script 生成的脚本 ──直连──► 被控 MySQL
GitHub ────HTTPS──────► updater（自更新）
data/ 目录 ◄──落盘──────── local_store(SQLite) · metrics(JSON) · .secret.key · tools/ · updates/
MySQL 系统库 ◄─PyMySQL──── system_db.StorageBackend（full 模式）
```

## 5. 策略/数据同步点（改一处必须同步的地方）

| 同步点 | 涉及位置 | 原因 |
|---|---|---|
| 三级运行时解析顺序 | `src/runtime_resolver.py` + `platforms/win64/scripts/_resolve_python.bat` + `install.bat` 内分支（linux 侧 start.sh 同型逻辑） | bat 在无 Python 时无法调 Python 模块，策略被迫重复实现 |
| 设置键全集 | 新键必须加入 `config_store.DEFAULT_SETTINGS` | 旧配置自动补齐机制依赖该表 |
| 版本号 | `src/version.py`（权威）→ pyproject.toml → package.json → 两份 README 徽章；**ci.yml 中 `--tag v3.8.1` 为硬编码需手动** | sync_version.py 不覆盖 ci.yml |
| 统一任务模型 | `schedule_store._default_task` ↔ `system_db._task_to_row/_row_to_task`（extra JSON 6 字段） | 双后端字段对齐 |
| mc_connection 列 | `local_store._migrate` ↔ `system_db._ensure_conn_cols` | lite/full 列集一致（SSH×7 + 备份 3 列） |
| 平台脚本 | `platforms/<os>/scripts/` 为准，顶层 `scripts/` 为构建回退副本 | 单仓库双目录 |
