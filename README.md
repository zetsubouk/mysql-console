# MySQL Console

<p align="center">
  🔖&nbsp;<strong>语言 / Language:</strong>&nbsp;
  <a href="README.md"><img src="https://img.shields.io/badge/%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-6366f1?style=for-the-badge" alt="简体中文"></a>&nbsp;
  <a href="README.en.md"><img src="https://img.shields.io/badge/English-0EA5E9?style=for-the-badge" alt="English"></a>
</p>

**零框架的 MySQL 可视化管理平台** — 单个 Python 服务 + 浏览器,即可完成 MySQL 的监控、备份、用户管理全流程。
被管理的数据库可以是本机实例,也可以是任意网络可达的独立服务器——**部署机无需安装 MySQL**。

![version](https://img.shields.io/badge/version-3.8.0-34d399) ![python](https://img.shields.io/badge/python-3.10%2B-22d3ee) ![deps](https://img.shields.io/badge/deps-pymysql%20%2B%20cryptography-a78bfa) ![platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-fbbf24) ![ci](https://img.shields.io/badge/CI-三级回归-94a3b8) ![license](https://img.shields.io/badge/license-MIT-fb7185)

---

## ✨ 为什么是 MySQL Console

同类工具要么重(Web 版 phpMyAdmin 需 PHP + Web 服务器),要么需要本地装 MySQL。
MySQL Console 把它压到极致:

| | |
|---|---|
| 🪶 **零框架** | Python 标准库 `http.server` 起服务,运行依赖仅 `pymysql` + `cryptography` 两个包 |
| 📦 **无 Python 也能装** | 本机没装 Python?`install.bat` 三级解析:内置运行时 → 系统 Python(建隔离 venv,**绝不改动系统环境**)→ 自动下载私有运行时(官方源+国内镜像);完整包连下载都省了,全程离线 |
| 🖥 **本机无 MySQL 也能跑** | 被管库在本机或远程皆可;mysqldump/mysql 客户端工具三级动态探测,缺什么向导直接告诉你 |
| 🌐 **真跨平台** | Windows / Linux / macOS:一键安装脚本、systemd 服务化、原生文件对话框(Win32 / osascript / zenity)全覆盖 |
| 📊 **带进度的备份还原** | mysqldump 流式管道 + 字节级/表级实时进度,gzip 流式压缩,不是"转圈等结果" |
| ⏰ **定时备份双引擎** | 内置调度线程(免注册)+ 系统计划任务(schtasks/systemd/cron)可选,多任务、保留策略 |
| 🔐 **开箱安全** | 登录认证 + 失败锁定 + 找回密码,连接凭据 Fernet 加密存储 |
| 🔄 **软件自更新** | 检查 GitHub Releases → 下载校验 → 备份 → 自更新重启,一条龙 |

## 🗺 架构总览

打开 **[docs/architecture.html](docs/architecture.html)** 查看交互友好的完整架构图(分层组件、数据流、图例),整体结构:

```
浏览器 SPA (ECharts)  ──HTTP:8090──▶  server.py (ThreadingHTTPServer, 62 个 REST API)
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

## 🚀 快速开始

> 详细部署(双平台、开机自启、systemd、远程库配置、FAQ)见 **[docs/INSTALL.md](docs/INSTALL.md)**。

### 1️⃣ 获取项目

```bash
git clone https://github.com/zetsubouk/mysql-console.git
cd mysql-console
```

> 无 Python 环境?直接用发布页的 **full-win64 完整包**(内置运行时,安装全程离线),
> 或运行 `install.bat` 按提示确认后自动下载私有运行时(约 11MB,只装进项目目录,不碰系统)。

### 2️⃣ 一键安装 + 启动

**Windows**(双击即可):

```bat
scripts\install.bat    :: 建 .venv + 装依赖
scripts\start.bat      :: 启动服务
```

**Linux / macOS:**

```bash
./scripts/install.sh   # 或发布包根目录 ./install.sh
./scripts/start.sh
sudo ./scripts/install.sh --service   # Linux 生产推荐: systemd 开机自启
```

### 3️⃣ 三步向导

浏览器打开 `http://127.0.0.1:8090`,按向导完成:**环境检测 → MySQL 客户端目录 → 数据库连接**。
没有 mysqldump?向导会如实告诉你缺什么、装什么。

> 恢复出厂:`init.bat` / `init.sh`(删除全部配置、系统库与备份,慎用)。

## 📦 功能一览

### 监控与运维
- **实时监控**:连接/QPS/慢查询/线程,`/api/monitor` 增量刷新
- **数据看板**:健康评分、InnoDB 分析、表空间、主从复制状态
- **库表管理**:数据库/表统计、大小排行
- **用户管理**:MySQL 用户增删改、授权编辑(带现状回填)、root 保护
- **进程管理**:进程列表、Kill 连接
- **告警中心 + 服务器变量**:阈值可配置,变量含义说明

### 备份与还原(核心)
- 手动备份:全库/多库,gzip 压缩,**字节级 + 表级双维度实时进度**
- 还原:自动识别备份包是否含建库语句,自动补建目标库
- 备份历史 + 文件浏览器 + 下载接口(路径白名单防任意文件读取)
- 定时备份:多任务、保留策略、双引擎(内置调度 / 系统计划任务)
- **本地/远程存储自动判定**:`localhost/127.0.0.1` 等本地地址 → 备份落本地 `backup_dir`,
  可下载复制;其它地址 → 经 **SSH 管道直写服务器** `remote_backup_dir`(默认 `~/mysql-console-backups`),
  **不落本地、不占用本机磁盘与网络带宽**,下载按钮置灰、仅提供"远程还原"
- **SSH 隧道**:3306 直连不通时,用连接配置的 SSH 跳板机做本地端口转发后备份/还原(仅密钥认证)
- **内置 MySQL 客户端**:发布包可用 `build_release.py --tools-dir <bin目录>` 附上 mysqldump/mysql 及 sha256 清单,
  客户机零安装即可远程备份(探测链自动落位 tools/)
- 备份目录与远程目录均为**每连接独立配置**(连接管理 → 编辑连接)

### 平台能力
- 登录认证、失败锁定、找回密码、用户名修改
- 双后端存储:轻量模式(SQLite,零依赖 MySQL)/ 全量模式(系统库入 MySQL,可切换)
- MySQL 服务状态检测与重启、系统资源(CPU/内存)监控
- 软件自更新(GitHub Releases 检查/下载/校验/备份/重启)
- 首次运行三步向导、`MC_DATA_DIR` 数据目录重定位、便携部署
- 自带运行时:三级解析(内置/系统 venv/私有下载),版本不满足时交互确认,系统 Python 零改动

## 🧪 测试与质量

```bash
npm ci && npm test                 # 前端 jsdom 回归(6 套)
python tests/api/test_api.py       # API 层回归(隔离数据目录,无需 MySQL)
python tests/unit/test_units.py    # 离线单元测试
python tests/e2e/test_e2e.py       # 备份→还原端到端(需 MySQL)
```

全部由 `.github/workflows/ci.yml` 三级流水线自动执行:后端矩阵 → 前端 jsdom → E2E(MySQL 8 服务容器)。

## 📁 项目结构

```
mysql-console/
├── src/                  # 全部 Python 源码 + static/(前端,零构建)
│   ├── server.py         # HTTP 服务入口: API + 静态资源 + 调度 + 原生对话框
│   ├── backup_engine.py  # 备份/还原引擎(流式管道 + 进度)
│   ├── mysql_client.py   # PyMySQL 查询封装(监控/库表/用户/进程)
│   ├── config_store.py   # Fernet 加密连接配置 + 设置
│   ├── system_db.py      # 双后端存储(轻量 SQLite / 全量 MySQL 系统库)
│   ├── schedule_store.py / native_scheduler.py   # 定时备份双引擎
│   ├── env_probe.py / service_manager.py / sys_resources.py
│   ├── updater.py / paths.py / ...
│   └── static/           # index.html / app.js / login.html / ECharts(本地)
├── docs/                 # INSTALL / RELEASE / DEVLOG / HANDOFF / architecture.html
├── scripts/              # install/start/stop/init (.bat/.sh) + systemd 模板 + 构建脚本
├── tests/                # api/ unit/ e2e/ frontend/ 四型测试
├── .github/workflows/    # 三级 CI
├── data/                 # 运行时数据(不入库; MC_DATA_DIR 可重定位)
└── runtime/              # 自带独立运行时(不入库; 完整包内置或 install.bat 下载)
```

## 📖 文档

| 文档 | 内容 |
|---|---|
| [docs/INSTALL.md](docs/INSTALL.md) | 部署指南(双平台 / systemd / 远程库 / FAQ) |
| [docs/architecture.html](docs/architecture.html) | 系统架构图(暗色 SVG,浏览器直接打开) |
| [docs/RELEASE.md](docs/RELEASE.md) | 发版流程 |
| [docs/DEVLOG.md](docs/DEVLOG.md) | 开发演进史 |
| [docs/HANDOFF.md](docs/HANDOFF.md) | AI/开发者交接指南 |
| [docs/MIGRATION.md](docs/MIGRATION.md) | 版本迁移说明 |

## 📄 License

MIT
