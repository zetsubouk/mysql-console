# MySQL Console

<p align="center">
  <img src="docs/images/overview.png" alt="MySQL Console 主控台" width="920">
</p>

<p align="center">
  <strong>零框架的 MySQL 可视化管理平台</strong><br>
  单个 Python 服务 + 浏览器，完成 MySQL 的监控、备份、用户管理全流程<br>
  被管库在本机或任意远程服务器皆可 —— <strong>部署机无需安装 MySQL</strong>
</p>

<p align="center">
  🔖&nbsp;<strong>语言 / Language:</strong>&nbsp;
  <a href="README.md"><img src="https://img.shields.io/badge/%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-6366f1?style=for-the-badge" alt="简体中文"></a>&nbsp;
  <a href="README.en.md"><img src="https://img.shields.io/badge/English-0EA5E9?style=for-the-badge" alt="English"></a>
</p>

<p align="center">
  <a href="https://github.com/zetsubouk/mysql-console/releases"><img src="https://img.shields.io/badge/version-3.8.1-34d399" alt="version"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-22d3ee" alt="python">
  <img src="https://img.shields.io/badge/deps-pymysql%20%2B%20cryptography-a78bfa" alt="deps">
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-fbbf24" alt="platform">
  <a href="https://github.com/zetsubouk/mysql-console/actions/workflows/ci.yml"><img src="https://github.com/zetsubouk/mysql-console/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <img src="https://img.shields.io/badge/license-MIT-fb7185" alt="license">
</p>

---

## ✨ 为什么是 MySQL Console

同类工具要么重（phpMyAdmin 需要 PHP + Web 服务器），要么要求本地装 MySQL。MySQL Console 把它压到极致：

| | |
|---|---|
| 🪶 **零框架** | Python 标准库 `http.server` 起服务，运行依赖仅 `pymysql` + `cryptography` 两个包，前端零构建 |
| 📦 **无 Python 也能装** | 三级解析：内置运行时 → 系统 Python（隔离 venv，**绝不改动系统环境**）→ 自动下载私有运行时（官方源+国内镜像）；完整包连下载都省了，全程离线 |
| 🖥 **本机无 MySQL 也能跑** | 被管库在本机或远程皆可；mysqldump/mysql 客户端四级动态探测，缺什么向导直接告诉你，也可向导内一键下载 |
| 📊 **带实时进度的备份还原** | mysqldump 流式管道 + **字节级/表级双维度实时进度** + gzip 流式压缩，不是"转圈等结果" |
| ⏰ **定时备份双引擎** | 内置调度线程（免注册）+ 系统计划任务（schtasks/cron）可选，多任务、保留策略 |
| 🌐 **真跨平台** | Windows / Linux / macOS：一键安装、systemd 服务化、原生文件对话框（Win32 / osascript / zenity） |
| 🔐 **开箱安全** | 登录认证 + 失败锁定 + 找回密码，连接凭据 Fernet 加密存储，非回环部署强制访问令牌 + 可选 TLS |
| 🔄 **软件自更新** | 检查 GitHub Releases → 下载校验 → 备份 → 自更新重启，一条龙 |

## 🖼 界面预览

| 数据看板 | 备份与还原 |
|---|---|
| <img src="docs/images/dashboard.png" alt="数据看板：健康评分 / 表空间 / InnoDB / 复制状态" width="440"> | <img src="docs/images/backup.png" alt="备份与还原：实时进度 / 历史 / 文件浏览器" width="440"> |

| SQL 查询（只读守卫 + 多页签） | 登录认证 |
|---|---|
| <img src="docs/images/query.png" alt="SQL 查询：只读语句守卫 / 结果集 / 耗时统计" width="440"> | <img src="docs/images/login.png" alt="登录页：管理员登录 / 找回密码 / 访问令牌" width="243"> |

> 全部截图均来自本项目的真实运行界面（MariaDB 10.11 演示数据）。交互式架构图见 [docs/architecture.html](docs/architecture.html)。

## 🎬 60 秒体验

```bash
git clone https://github.com/zetsubouk/mysql-console.git
cd mysql-console
./platforms/linux/scripts/install.sh    # Windows 双击 platforms\win64\scripts\install.bat
./platforms/linux/scripts/start.sh      # 启动服务（发布包解压后根目录即有 install）
```

浏览器打开 **<http://127.0.0.1:8090>**，按五步向导完成：**本机数据库 → 环境检测 → MySQL 客户端目录 → 运行模式 → 数据库连接**，即可管理你的第一台 MySQL。本机没有数据库？向导可从官方源静默代装（版本/路径/字符集/内存参数全程可配）。

**体验一次带进度的备份**（也可完全在页面上操作）：

```bash
# 发起备份 → 立即返回任务 ID（202）
$ curl -s -X POST http://127.0.0.1:8090/api/backup \
    -H 'Content-Type: application/json' -d '{"dbs": ["shop"], "gzip": true}'
{"task_id": "518dc24b6eea", "ok": true}

# 轮询进度：字节级百分比 + 当前正在备份的表
$ curl -s http://127.0.0.1:8090/api/task/518dc24b6eea
{"id": "518dc24b6eea", "kind": "backup", "status": "done", "phase": "完成",
 "percent": 100.0, "current": "", "message": "备份成功: backups/shop_20260906_040939.sql.gz",
 "detail": "结果: 成功(80.8 KB, 0.2s)", "elapsed": 0.2}
```

> 无 MySQL 可练手？`tests/e2e/` 有完整的备份→还原闭环脚本；或参考 [docs/INSTALL.md](docs/INSTALL.md) 用 Docker 起一个 MySQL 8。

## 🗺 架构总览

```
浏览器 SPA (ECharts)  ──HTTP:8090──▶  server.py (ThreadingHTTPServer, 78 条 REST API)
                                        │
        ┌──────────┬──────────┬────────┼──────────┬──────────┐
   mysql_client  backup_   定时备份   config_store  service/   updater
   监控/用户/进程  engine   双引擎     /system_db   env_probe  自更新
   数据看板      备份/还原  调度/注册  Fernet 加密   告警/变量   Releases
        │           │         │         │           │
        ▼           ▼         ▼         ▼           ▼
   MySQL Server  mysqldump  schtasks/  data/(SQLite/  OS API
   (本机或远程)   / mysql    systemd/   backups/日志)  (对话框等)
                 子进程管道   cron
```

- **分层组件图 / 类图 / 部署图**（Mermaid，成员名经源码核验）：[docs/wiki/09-class-and-component-diagrams.md](docs/wiki/09-class-and-component-diagrams.md)
- **11 张关键时序图**（请求 / 认证 / 备份 / 调度 / 自更新）：[docs/wiki/08-sequence-diagrams.md](docs/wiki/08-sequence-diagrams.md)
- **完整架构文档**：[docs/wiki/01-architecture.md](docs/wiki/01-architecture.md)

## 🚀 快速开始

> 详细部署（双平台、开机自启、systemd、远程库配置、FAQ）见 **[docs/INSTALL.md](docs/INSTALL.md)**。

### 1️⃣ 获取项目

```bash
git clone https://github.com/zetsubouk/mysql-console.git
cd mysql-console
```

> 无 Python 环境？发布页按需选 **standard（正常版，内置 MySQL 客户端，开箱即用）** 或 **slim（瘦版 ~600K，向导提示下载/跳过）**；或运行 `install.bat` 自动下载私有运行时（约 11MB，只装进项目目录，不碰系统）。

### 2️⃣ 一键安装 + 启动

发布包按平台与形态分 4 包（`--platform × --variant`），按需下载其一即可：

| 包 | 文件名示例 | 大小 | 含 MySQL 客户端 | 适用 |
|---|---|---|---|---|
| normal-win64 | `mysql-console-3.8.1-win64.zip` | ~30MB | 是（双版本 5.7+8.x） | Windows 开箱即用 |
| normal-linux | `mysql-console-3.8.1-linux.tar.gz` | ~30MB | 是 | Linux/macOS 开箱即用 |
| slim-win64 | `mysql-console-3.8.1-slim-win64.zip` | ~600K | 否，向导可下载/跳过 | 轻量，需自备或向导下载 |
| slim-linux | `mysql-console-3.8.1-slim-linux.tar.gz` | ~600K | 否 | 同上 |

**Windows**（双击即可，发布包在根目录）：

```bat
platforms\win64\scripts\install.bat    :: 建 .venv + 装依赖
platforms\win64\scripts\start.bat      :: 启动服务
```

**Linux / macOS**（按 linux 模式）：

```bash
./platforms/linux/scripts/install.sh   # 或发布包根目录 ./install.sh
./platforms/linux/scripts/start.sh
sudo ./platforms/linux/scripts/install.sh --service   # Linux 生产推荐: systemd 开机自启
```

### 3️⃣ 五步向导

浏览器打开 `http://127.0.0.1:8090`，按向导完成：**本机数据库 → 环境检测 → MySQL 客户端目录 → 运行模式 → 数据库连接**。

- **第 1 步·本机数据库**：自动检测本机是否已有 MySQL/MariaDB；检测不到时可选择由向导代装——版本列表取自官方源（LTS 优先，多镜像下载）、安装路径与数据目录自定义、字符集与内存/连接参数按本机资源给建议值、可选注册系统服务开机自启。选择不安装时，自动转入既有客户端工具检测/下载流程。
- 瘦版未内置客户端时向导会提供**下载/跳过**（standard 版静默跳过）。

> 恢复出厂：`init.bat` / `init.sh`（删除全部配置、系统库与备份，慎用）。

## 📦 功能一览

<details open>
<summary><b>监控与运维</b></summary>

- **实时监控**：连接/QPS/慢查询/线程，`/api/monitor` 增量刷新，5s/15min/1h 多窗口
- **数据看板**：健康评分（0-100）、InnoDB 分析、表空间占比与 Top10、主从复制状态
- **库表管理**：数据库/表统计、大小排行
- **用户管理**：MySQL 用户增删改、授权编辑（带现状回填）、root 保护
- **进程管理**：进程列表、Kill 连接
- **SQL 查询**：只读语句守卫（拦截可执行注释/WITH+DML/SET GLOBAL/多语句）、多页签、500 行截断
- **告警中心 + 服务器变量**：阈值可配置，87 个高频变量离线中文说明
- **AI 助手**（可选）：自然语言生成只读 SQL、EXPLAIN 优化建议、告警周报（OpenAI 兼容接口）

</details>

<details open>
<summary><b>备份与还原（核心）</b></summary>

- 手动备份：全库/多库，gzip 压缩，**字节级 + 表级双维度实时进度**
- 还原：自动识别备份包是否含建库语句，自动补建目标库
- 备份历史 + 文件浏览器 + 下载接口（路径白名单防任意文件读取）
- 定时备份：多任务、保留策略、双引擎（内置调度 / 系统计划任务）
- **本地/远程存储自动判定**：`localhost/127.0.0.1` → 备份落本地；其它地址 → 经 **SSH 管道直写服务器**，不落本地、不占本机磁盘与带宽
- **SSH 隧道**：3306 直连不通时经跳板机做本地端口转发（仅密钥认证）
- **内置 MySQL 客户端**：多版本（5.7/8.x）按连接声明的数据库版本族自动匹配合适工具，避免跨版本兼容问题

</details>

<details open>
<summary><b>平台能力</b></summary>

- 登录认证、失败锁定、找回密码、用户名修改
- 双后端存储：轻量模式（SQLite，零依赖 MySQL）/ 全量模式（系统库入 MySQL，可切换）
- MySQL 服务状态检测与重启、系统资源（CPU/内存）监控
- 软件自更新（GitHub Releases 检查/下载/校验/备份/重启）
- 首次运行五步向导（本机数据库检测与静默代装 MySQL / 环境检测 / 客户端 / 运行模式 / 连接）、`MC_DATA_DIR` 数据目录重定位、便携部署

</details>

## 🧪 测试与质量

```bash
npm ci && npm test                 # 前端 jsdom + vitest 回归（6 套）
python tests/api/test_api.py       # API 层回归（隔离数据目录，无需 MySQL）
python tests/unit/test_units.py    # 离线单元测试（30+ 项）
python tests/e2e/test_e2e.py       # 备份→还原端到端（需 MySQL）
```

全部由 [`.github/workflows/ci.yml`](.github/workflows/ci.yml) 流水线自动执行：**后端矩阵（3.10/3.11/3.12）→ 跨平台真机冒烟（Win/macOS）→ 前端 jsdom → E2E（MySQL 8 容器）→ systemd 容器全链路**。

## 📁 项目结构

```
mysql-console/
├── src/                  # 全部 Python 源码 + static/(前端,零构建)
│   ├── server.py         # HTTP 服务入口: API + 静态资源 + 调度 + 原生对话框
│   ├── backup_engine.py  # 备份/还原引擎(流式管道 + 进度)
│   ├── mysql_client.py   # PyMySQL 查询封装(监控/库表/用户/进程)
│   ├── config_store.py   # Fernet 加密连接配置 + 双模式存储适配
│   ├── system_db.py      # 全量模式系统库(6 张 mc_* 表 + StorageBackend)
│   ├── schedule_store.py / native_scheduler.py   # 定时备份双引擎
│   ├── env_probe.py / updater.py / runtime_resolver.py / ...
│   └── static/           # index.html / app.js / login.html / ECharts(本地)
├── docs/                 # INSTALL / RELEASE / DEVLOG / HANDOFF / architecture.html
├── docs/wiki/            # ⭐ Code Wiki:架构 / 模块 / API / 时序图 / 类图 / 部署图
├── platforms/            # 单仓库双目录：win64 / linux（mac 按 linux）
├── scripts/              # 构建共用脚本（build_release.py / sync_version.py）
├── tests/                # api/ unit/ e2e/ frontend/ vitest 五型测试
├── .github/workflows/    # CI 流水线（5 job）
├── data/                 # 运行时数据(不入库; MC_DATA_DIR 可重定位)
└── runtime/              # 自带独立运行时(不入库; 完整包内置或 install 下载)
```

## 📖 文档

| 文档 | 内容 |
|---|---|
| ⭐ [docs/wiki/README.md](docs/wiki/README.md) | **Code Wiki 导航**：架构 / 模块职责 / 类与函数 / 依赖 / 运行 / API / 测试 |
| [docs/wiki/01-architecture.md](docs/wiki/01-architecture.md) | 整体架构：分层、请求生命周期、线程模型、认证模型 |
| [docs/wiki/06-api-reference.md](docs/wiki/06-api-reference.md) | 83 条 REST API 全量地图（按业务域分组） |
| [docs/wiki/08-sequence-diagrams.md](docs/wiki/08-sequence-diagrams.md) | 11 张核心时序图（备份 / 调度 / 自更新…） |
| [docs/wiki/09-class-and-component-diagrams.md](docs/wiki/09-class-and-component-diagrams.md) | 类图 / 组件图 / 部署图 |
| [docs/INSTALL.md](docs/INSTALL.md) | 部署指南（双平台 / systemd / 远程库 / FAQ） |
| [docs/architecture.html](docs/architecture.html) | 系统架构图（暗色 SVG，浏览器直接打开） |
| [docs/RELEASE.md](docs/RELEASE.md) | 发版流程 |
| [docs/DEVLOG.md](docs/DEVLOG.md) | 开发演进史 |
| [docs/HANDOFF.md](docs/HANDOFF.md) | AI/开发者交接指南 |

## 🤝 参与贡献

欢迎 Issue 与 PR。改动后请跑通 [测试清单](docs/wiki/07-testing-and-ci.md#二改动后验证清单项目约定)；涉及架构请先读 [HANDOFF.md](docs/HANDOFF.md) 的避坑清单。

## 📄 License

[MIT](LICENSE)
