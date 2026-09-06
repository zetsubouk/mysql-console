# 02 · 主要模块职责

> 覆盖 `src/` 全部 27 个 Python 模块 + 前端 + 构建/平台脚本。行数为 `wc -l` 实测。

## 0. 模块全景图

```
核心服务层(6)      server / routes / handlers / paths / security / version
数据存储层(6)      config_store / system_db / local_store / mysql_client / metrics / variable_docs
备份与调度层(7)    backup_engine / schedule_store / native_scheduler / native_script
                   ssh_tunnel / cli_backup / cli_init
平台能力层(8)      env_probe / service_manager / sys_resources / updater
                   tools_downloader / runtime_resolver / pip_bootstrap / ai_client
前端(6 个文件)     app.js / index.html / login.html / style.css / dashboard-helpers.js / echarts.min.js
构建与平台脚本     scripts/build_release.py / sync_version.py / regen_manifest.py
                   platforms/{win64,linux}/scripts/*
```

## 1. 核心服务层

| 模块 | 行数 | 职责 |
|---|---|---|
| [server.py](../../src/server.py) | 305 | **HTTP 传输层 + 进程入口**。`Handler` 类（多重继承 HandlerBase）负责 HTTP 编解码、静态资源、下载流、认证守卫编排；PUT/DELETE 内联路由（连接/任务/用户/设置/备份记录的路径参数操作）；`main()` 启动流程（UTF-8 重配置 → 3 守护线程 → 部署安全门槛 → ThreadingHTTPServer → 可选 TLS）。关键常量：`HOST=MC_HOST 默认 127.0.0.1`、`PORT=MC_PORT 默认 8090` |
| [routes.py](../../src/routes.py) | 152 | **声明式路由注册表 + 分发器**（2026-08-31 从 if-elif 巨链拆出）。每条路由 = `(匹配器, 目标方法名, argkind)`；导入时预建精确 dict（O(1)）+ 前缀列表（按长度降序）；`dispatch()` 反射调用 Handler 方法。39 GET + 39 POST；**PUT/DELETE 不在表中** |
| [handlers.py](../../src/handlers.py) | 1745 | **业务处理层**（最大模块）。约 60 个 `g_*`/`p_*`/`_handle_*` 处理器；共享运行状态（会话表/重置码/更新缓存/激活连接）；3 个后台循环；Win32 `OPENFILENAMEW`/`BROWSEINFOW` + osascript + zenity 原生文件对话框（子线程执行、600s 超时）；初始化后自动生成 start/stop/init 脚本。**刻意不反向 import server.py（防循环依赖）** |
| [paths.py](../../src/paths.py) | 44 | **路径单一来源**。`APP_ROOT` 解析（兼容 src 布局/pip 安装/旧平铺）；`DATA_DIR = MC_DATA_DIR > <APP_ROOT>/data/ > ~/.mysql-console`；`static_dir()` 兜底读取 pip 数据文件路径 |
| [security.py](../../src/security.py) | 175 | **安全加固**。访问令牌常数时间比较；`bind_port()`（MC_PORT）；CSRF `origin_allowed`；TLS 自签证书生成与 socket 包装；`is_loopback()` |
| [version.py](../../src/version.py) | 3 | 版本号单一来源 `__version__ = "3.8.1"`（sync_version.py 据此同步全仓库） |

## 2. 数据存储层

| 模块 | 行数 | 职责 |
|---|---|---|
| [config_store.py](../../src/config_store.py) | 633 | **双模式适配器（数据层总入口）**。Fernet 加密（密钥 `data/.secret.key` 惰性生成，解密失败容错返回空串）；管理员密码 PBKDF2（20 万轮）；`DEFAULT_SETTINGS` 26 键是设置键全集唯一声明处（读时兜底、写时白名单过滤）；系统库可达性探测带 TTL 缓存（成功 5s/失败 2s + 锁）；`switch_to_full_mode()` 不可逆切换；旧 config.json 导入时自动迁移 |
| [system_db.py](../../src/system_db.py) | 829 | **全量模式系统库管理**。`init_system_db()` 建 `_mysql_console` 库 + 6 张 `mc_*` 表（InnoDB/utf8mb4）；`StorageBackend` 全 CRUD（每方法短连接 + 幂等补列）；`import_from_file()` SQLite→MySQL 迁移；统一任务模型 ↔ `mc_schedule` 行双向映射（freq/time 等 6 字段打包 JSON 进 `extra` 列，旧 cron_expr 可反解兼容） |
| [local_store.py](../../src/local_store.py) | 266 | **轻量存储**。SQLite `data/config.db`（WAL）；三表 `meta/connections/settings`；`_db()` 上下文管理器保证连接关闭（Windows WAL 不关会锁文件）；`PRAGMA table_info` 幂等补列（SSH×7 + 备份 3 列）；`reset_all()`/`clear_lite_data()`（切全量后仅留 bootstrap 相关 meta） |
| [mysql_client.py](../../src/mysql_client.py) | 778 | **被控 MySQL 查询封装**（独立于存储层，只依赖 pymysql）。连接/测试；监控（overview/monitor 增量/monitor_full 深度/health_score 评分/innodb/tablespace/replication 兼容 5.7~8.4 REPLICA→SLAVE 回退/alerts 阈值）；库表统计（排除 4 系统库）；进程列表/Kill；用户管理（标识符白名单 + `_ALLOWED_PRIVS` + 4 种预设）；**只读 SQL 查询器**（前导白名单 + 四类绕过拦截：可执行注释/WITH+DML/SET GLOBAL/多语句）；AI schema 上下文 |
| [metrics.py](../../src/metrics.py) | 230 | **告警/健康历史采样器**（JSON 落盘，与 HTTP 层零耦合可独立单测）。每分钟粒度写 `alerts_history.json`（同分钟合并、保留 7 天、超 1600 点 rollup、tmp + `os.replace` 原子替换、线程锁）；健康分更早数据小时级均值；`ai_report_context()` 汇总为 AI 报告文本 |
| [variable_docs.py](../../src/variable_docs.py) | 99 | **服务器变量离线中文词典**。约 87 个高频变量含义；未收录返回空串**不杜撰**；被 `mysql_client.variables()` 使用 |

## 3. 备份与调度层

| 模块 | 行数 | 职责 |
|---|---|---|
| [backup_engine.py](../../src/backup_engine.py) | 1325 | **备份/还原引擎**。`run_backup` 双层参数签名（`conn_cfg` 隧道化端点 vs `storage_cfg` 原始 host 判定存储位置）；`_dump_to_file`/`_dump_to_remote` 流式管道（1MB 分块 + 双 stderr 线程解析表切换与错误）；gzip level=6；内存 `TASKS` 任务管理器 + task_id 轮询；restore 含建库语句识别（前 256KB）、zip 成员 basename 防穿越、远程 zip 拒绝；备份文件浏览与下载白名单校验；历史记录双后端落库 |
| [schedule_store.py](../../src/schedule_store.py) | 328 | **定时任务存储与到点匹配**。统一任务模型（engine/freq/weekday 0=周日/time/at_once/keep…）+ `_normalize_task` 校验归一；`is_due()` 到点判定（**tm_wday 需 `(wday+1)%7` 平移**）；旧单任务 config.json/`schedule_tasks.json` 自动迁移并关旧开关防双跑；`describe()` 人性化周期文案 |
| [native_scheduler.py](../../src/native_scheduler.py) | 251 | **OS 计划任务适配**。Windows→schtasks（`_win_sch_args` 映射五种 freq）；Linux/macOS→crontab（marker `#mysqlconsole:<id>` 幂等去重）；`unregister` 幂等成功；注册失败时 `gen_command()` 生成可手动执行的兜底命令 |
| [native_script.py](../../src/native_script.py) | 407 | **自包含备份脚本生成器**（计划任务只调脚本不经 Python）。Windows `.ps1`（UTF-8 BOM+CRLF）/ Linux `.sh`（chmod 700）；脚本内：探测 mysqldump（配置目录→PATH）→ mysqldump → 校验（MIN_SIZE 防空包）→ gzip → keep 清理 → 日志；密码明文内嵌（单引号转义） |
| [ssh_tunnel.py](../../src/ssh_tunnel.py) | 335 | **SSH 辅助层**（仅密钥认证，密码登录不支持）。本地端口转发隧道（`ssh -N -T -L`，8s 就绪轮询，注册表 + 幂等清理）；远程只读工具集（执行命令/`gzip -dc \| wc -c` 取大小/读流/`probe_remote_env` 探测远端 OS 与 Git Bash） |
| [cli_backup.py](../../src/cli_backup.py) | 83 | **定时备份 CLI**（历史遗留入口）：`--task <id>` / `--list`；退出码 0=成功/跳过 1=失败 |
| [cli_init.py](../../src/cli_init.py) | 246 | **一键初始化 CLI**（恢复出厂）。`--check` 只读预览将删数据；`--do [--force]` 执行：全量模式 DROP 系统配置库 + 删 config.db/.secret.key/logs/备份文件；**绝不碰生产业务库**；交互确认 |

## 4. 平台能力层

| 模块 | 行数 | 职责 |
|---|---|---|
| [env_probe.py](../../src/env_probe.py) | 404 | **MySQL 客户端动态定位 + 环境探测**。`find_tool()` 四级链（用户配置→内置 tools/→PATH→常见目录；内置优先于 PATH）；内置工具 SHA256 惰性校验（mtime+size 失效）；`find_tool_versioned()` 按数据库版本族（5/8）选工具；`env_summary()` 供 `/api/setup/env` 与三步向导 |
| [service_manager.py](../../src/service_manager.py) | 181 | **本机 MySQL 服务管理**（远程库返回 unknown）。Windows `net`/`sc`、Linux `systemctl`、macOS `brew services`；四态 status；restart 带 verify 回调轮询（默认 90s 超时） |
| [sys_resources.py](../../src/sys_resources.py) | 218 | **系统资源采集**（零第三方硬依赖）。CPU/内存/磁盘：Windows ctypes（GetSystemTimes/GlobalMemoryStatusEx/GetDiskFreeSpaceExW）、Linux `/proc` + `statvfs`；IOPS/网络吞吐仅 psutil 可用时提供（缺失返回 None 前端隐藏图表） |
| [updater.py](../../src/updater.py) | 422 | **软件自更新**。check（GitHub API，失败依次回退：免验证 SSL 重试→本地缓存→随包 bundled_release.json 标记 offline）→ pick_asset（win 选 zip 其余 tar.gz）→ prepare（.part 临时文件 + 强校验大小 + SHA256 → 解压归一 → 代码备份到 `data/updates/backup/<ver>/`）→ apply（**独立进程** `apply_update.py`：等端口释放→删旧拷新（保留 data/.venv 等）→按原方式重启→update.log） |
| [tools_downloader.py](../../src/tools_downloader.py) | 234 | **瘦版向导后台下载 MySQL 客户端**（双版本 5.7+8.0）。4 个源逐试（MySQL 官方→阿里云→清华→cdn.mysql）；校验（≥5MB + `official_sha256.json` 基准 + 压缩包完整性）；只提取 mysqldump/mysql + Windows 运行库 DLL；幂等重写 tools 清单 |
| [runtime_resolver.py](../../src/runtime_resolver.py) | 341 | **三级运行时解析**（① 内置 runtime/python → ② 系统 Python 实测 ≥3.10 → ③ 下载嵌入式 3.12.10，官方→华为云→npmmirror）。防穿越解压；`._pth` 解注 `import site`（幂等）；`resolved_python.txt` 缓存；依赖安装双路（wheels 离线轮子自启动 / 在线可选清华镜像）；**绝不改用户系统环境** |
| [pip_bootstrap.py](../../src/pip_bootstrap.py) | 137 | **嵌入式运行时的 pip 在线引导**。三通道逐试：get-pip.py 官方→阿里云→清华 simple 索引解析最新 wheel；pip 轮子自启动安装自身（`python <whl>/pip install <whl>`） |
| [ai_client.py](../../src/ai_client.py) | 213 | **AI 辅助**（OpenAI 兼容 `/v1/chat/completions`，兼容 DeepSeek/通义/Ollama）。api_key Fernet 加密落库；60s 超时；异常统一转 `AiError`→400 可读错误；三场景：自然语言→只读 SQL（prompt 强约束白名单关键字）/ EXPLAIN 优化建议 / 告警健康周报；`_strip_code` 剥 LLM 代码围栏 |

## 5. 前端（src/static/，零构建）

| 文件 | 行数 | 职责 |
|---|---|---|
| [app.js](../../src/static/app.js) | 3314 | **全部前端逻辑**。头部自带 25 区块目录；基础设施：`api()`（双认证头 + 401 自动跳登录）、`MCUtils` 命名空间（fmtSize/fmtTime/esc 纯函数）、`confirmDialog`/`toast`、ICON SVG 库；功能区块：页面切换/连接管理/ECharts 图表工厂/监控轮询/备份进度弹窗 `showProgressModal` + `pollTask`/定时任务/SQL 查询多页签/用户管理 `parseGrants` 授权回填/向导/看板 `_dash*` 系列/更新角标。两条硬规范：新顶层逻辑必须容错非对象响应（jsdom fetch stub 只返回 `[]`）；inline onclick 桥一律 `window.xxx` 显式挂载 |
| [index.html](../../src/static/index.html) | 1116 | 主控制台骨架：sidebar（监控中心/资源管理/数据保护/系统四组导航）+ topbar（连接选择器/更新角标/主题切换）+ 13 个 section（欢迎横幅 + 12 功能页）；尾部只加载 echarts.min.js 与 app.js |
| [login.html](../../src/static/login.html) | 464 | 独立登录页：管理员登录表单（含可选访问令牌）+ 找回密码面板（6 位验证码显示在服务端终端）+ 内联脚本 |
| [style.css](../../src/static/style.css) | 688 | 全部样式（含暗色主题） |
| [dashboard-helpers.js](../../src/static/dashboard-helpers.js) | 48 | 6 个导出纯函数（健康趋势降采样/表空间联动过滤/在线状态判定等），**仅被 vitest 引用**；浏览器端运行的是 app.js 内同语义 `_dash*` 包装 |
| echarts.min.js | 本地库 | 图表库随包分发，无 CDN |

## 6. 构建与平台脚本

| 脚本 | 行数 | 职责 |
|---|---|---|
| [build_release.py](../../scripts/build_release.py) | 655 | 4 包矩阵一键构建：文件收集（`git ls-files` 白名单 + 安装包只含 install 启动器）→ 可选装配（`--with-runtime` 嵌入式 Python + pip 轮子预装；`--tools-dir` MySQL 客户端 + SHA256SUMS；standard 缺省时多镜像自动拉取官方工具）→ 打包 → **validate**（必需白名单 + 禁入黑名单 tests/.github/data 等） |
| [sync_version.py](../../scripts/sync_version.py) | 140 | 版本号单一来源（version.py）同步到 pyproject.toml / package.json / 两份 README 徽章；`--check` 可入 CI 门禁；不覆盖 ci.yml 硬编码版本（需手动） |
| [regen_manifest.py](../../scripts/regen_manifest.py) | 51 | `git ls-files` 全量重写 `docs/MANIFEST.txt`（sha256 前 16 位 + 大小 + 路径） |

**platforms/ 单仓库双目录**（构建时优先取 `platforms/<os>/scripts/`，回退顶层 `scripts/` 同名副本）：

| 脚本 | win64 | linux（macOS 按 linux） |
|---|---|---|
| install | [install.bat](../../platforms/win64/scripts/install.bat)（280 行）：解析运行时→建 .venv 或下载嵌入式 Python（curl→PowerShell 双通道三镜像）→解压+._pth 解注→依赖安装（在线引导/离线轮子）；支持 `--yes`/`--runtime-zip` | [install.sh](../../platforms/linux/scripts/install.sh)（121 行）：定位根→探测 Python→建 .venv→装依赖；`--service` 渲染 systemd unit 并 enable --now；`--print-service` 只打印审查 |
| start | [start.bat](../../platforms/win64/scripts/start.bat)：杀旧实例→解析运行时→依赖缺失**拒绝启动**且不碰系统 Python→UTF-8 启动 | [start.sh](../../platforms/linux/scripts/start.sh)：.venv 优先→依赖缺失自动 pip 装→`exec server.py` |
| stop | stop.bat：netstat 找 8090 → taskkill | stop.sh：lsof → kill → kill -9 |
| init | init.bat/init.sh：`cli_init.py --check` 预览 → 输 y 确认 → `--do --force` 恢复出厂 | 同左 |
| 服务模板 | —（示例用 schtasks） | [mysql-console.service](../../platforms/linux/scripts/mysql-console.service)：Type=simple、Restart=on-failure、`__BASE_DIR__`/`__USER__` 占位符 |
| 共享 | `_resolve_python.bat`：.venv→runtime\python→缓存→py -3→python（真实执行验证） | — |
