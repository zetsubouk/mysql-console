# MySQL Console - 数据库可视化管理平台

基于 Python 标准库 + PyMySQL 的本地 MySQL 管理 Web 应用,通过浏览器图形化管理 MySQL 数据库,支持系统状态监控、数据库统计、用户/连接查看,以及**备份与还原**核心功能。

## 功能总览

| 模块 | 能力 |
|---|---|
| 概览监控 | 服务器版本/运行时长/数据目录、连接数/线程/缓存命中率卡片、连接数与 QPS 实时趋势图(5 秒刷新)、**实时监控 Tab**(系统资源/InnoDB 深度/主从同步) |
| 界面主题 | **浅色/暗色双主题**一键切换(本地持久化)、侧栏分组导航、全 SVG 图标、健康阈值配色 |
| 数据库 | 每个库的表数量、数据大小、索引大小、总大小、字符集;点击查看库内表结构详情 |
| 用户与连接 | MySQL 用户列表(密码/权限/锁定状态)、实时进程列表(可 Kill 指定连接) |
| 备份与还原 | **备份**: 单库/多库/全库,输出到指定目录,gzip 压缩,时间戳命名;**还原**: 选择本地 .sql/.sql.gz 文件执行还原(二次确认);完整操作历史 |
| 任务进度 | 备份/还原**异步执行**,居中进度弹窗实时显示百分比与当前处理表(备份按表、还原按字节),可展开查看执行详情,期间阻止误操作 |
| 文件选择 | 备份目录/还原文件支持**调用 Windows 原生对话框**选择,也可用内置目录树浏览 |
| 定时备份 | cron 表达式定时自动备份,保留最近 N 份,自动清理旧备份 |
| 连接管理 | 多连接配置,密码加密存储(本地 .secret.key + Fernet),连接测试 |
| 操作日志 | 备份/还原/连接等操作全程留痕 |
| 登录认证 | 全量模式下 token 登录鉴权,Session 8 小时,支持修改密码/找回密码(终端验证码) |
| 数据看板 | 健康评分、InnoDB 指标、表空间 TOP、复制状态一览 |
| 告警中心 | 连接数/慢查询/活跃线程阈值检查(阈值可配置改造进行中) |
| 服务器变量 | SHOW VARIABLES 全量浏览,前端关键字过滤 |

## 快速启动

> 完整安装部署指南(含开机自启、systemd 服务化、远程数据库权限配置、FAQ)见 **INSTALL.md**。

### Windows
1. 双击 `install.bat`(首次:建 .venv 并装依赖;需先装 Python ≥3.10)
2. 双击 `start.bat`(自动探测 Python,缺依赖会自动安装)
3. 浏览器访问 http://127.0.0.1:8090

### Linux / macOS
```bash
./install.sh                 # 首次:建 .venv 并装依赖
./start.sh                   # 停止: ./stop.sh
# Linux 生产环境推荐注册 systemd 服务(开机自启):
sudo ./install.sh --service
```

### 首次部署
首次打开页面会自动进入**引导向导**:
1. **环境检测** —— Python/依赖/MySQL 客户端逐项检查(客户端缺失只影响备份还原,可后续补配);
2. **客户端与目录** —— 自动探测 mysqldump/mysql 位置,可手动修正并验证;
3. **运行模式选择** —— 轻量模式(文件存储)/ 全量模式(系统库+登录认证),全量模式自动创建系统库并设管理员;
4. **数据库连接** —— 填写要管理的 MySQL,**本机或远程服务器均可**,测试连接后保存激活。

也可在「连接管理 → 重新运行引导」随时重新配置;客户端目录等参数在「连接管理 → 服务设置」中调整。

> 被管理的 MySQL 可以是本机实例,也可以是任意网络可达的独立服务器——本服务只依赖 Python 与 MySQL 客户端工具,不要求本机装有数据库。

## 项目结构

```
mysql-console/
├── server.py          # HTTP 服务入口(API + 静态资源 + 内置定时调度 + 原生对话框)
├── config_store.py    # 连接配置加密存储(Fernet)+ 激活状态持久化
├── mysql_client.py    # MySQL 查询封装(PyMySQL)
├── backup_engine.py   # 备份/还原引擎(异步任务 + 表级/字节级进度)
├── schedule_store.py  # 定时备份任务存储(多任务模型 + 到点匹配 + 旧配置迁移)
├── native_scheduler.py# 系统计划任务适配(OS 自动识别, schtasks/systemd/crontab)
├── cli_backup.py      # 命令行执行入口(供系统计划任务调用)
├── system_db.py       # [全量模式] 系统库管理(建库6表 + StorageBackend 全 CRUD + 旧文件迁移)
├── static/            # 前端页面(index.html / app.js / style.css / login.html / echarts)
├── tests/             # 回归测试(test_frontend.js / test_e2e.py / test_progress.py)
├── data/
│   ├── config.json    # 连接配置(密码加密)+ 激活连接
│   ├── .secret.key    # 加密密钥(勿外泄)
│   ├── backups/       # 默认备份目录
│   ├── backup_history.json
│   └── logs/operations.log
├── DEVLOG.md          # 开发记录(Bug 修复历史/技术经验/优化建议)
├── start.bat          # 一键启动(自动清理旧实例)
└── README.md
```

## 测试

```bash
# 前端运行时回归(需 Node + jsdom)
NODE_PATH=<jsdom所在node_modules> node tests/test_frontend.js

# 备份→还原端到端(自动建测试库并清理,不碰生产数据)
python tests/test_e2e.py

# 异步任务进度验证
python tests/test_progress.py
```

更多开发背景、Bug 修复记录与后续优化建议见 **DEVLOG.md**。

## 备份与还原说明

- **备份**: 调用本机 MySQL 客户端 `mysqldump`,`--single-transaction` 在线一致性备份,含存储过程/触发器/事件;全库备份使用 `--all-databases`(含 mysql 系统库)。**压缩备份文件扩展名为 `.sql.gz`**(Windows 资源管理器默认隐藏扩展名,可在「查看」中开启显示)。
- **还原**: 调用本机 MySQL 客户端 `mysql`;备份文件若自带 `CREATE DATABASE` 语句则按其建库执行,否则还原到指定目标库。还原为覆盖式操作,**执行前会二次确认**。
- **备份目录**: 可点击「选择目录」调用 Windows 原生对话框,或「树」用内置目录树浏览;默认 `data\backups\`。
- **进度**: 执行时弹出居中进度窗,备份按表(需 mysqldump 逐表输出)计算百分比,还原按文件字节计算;「执行详情」可展开查看实时明细。
- **备份文件浏览/下载**: 备份页新增「备份文件」面板,列出备份目录内的 `.sql/.sql.gz` 文件(名称/大小/修改时间),一键下载;历史表中「备份成功」的记录也带「下载」按钮。下载走 `/api/backup-files/download`,支持 token 认证场景,并对路径做**白名单校验**(仅允许读取备份目录内文件,防任意文件读取)。

## 定时备份

支持**多任务管理**,在「定时备份」页可视化创建/编辑/启停/删除任务,无需接触 cron 表达式。

- **执行周期**: 下拉选择 每天 / 每小时 / 每周 / 每月 / 仅一次,并联动设置时间、星期、几号等参数
- **调度方式**(二选一):
  - **内置调度器**: Web 服务运行期间由 `server.py` 内部线程触发(默认)
  - **系统计划任务**: 自动识别操作系统注册到 Windows 计划任务(schtasks)/ Linux systemd timer 或 crontab,**服务关闭也照常执行**;保存时自动注册,删除任务自动反注册
- 每个任务独立设置: 备份范围(全部库/多选)、保留份数、绑定连接
- 任务列表显示周期描述(如"每周日 03:30")、启用状态、上次执行时间与结果
- 数据存储于 `data/schedule_tasks.json`;旧版单任务 cron 配置首次启动自动迁移
- 命令行入口 `cli_backup.py --task <id>` 供系统计划任务调用,`--list` 可列出任务

## 技术要点

- 后端: Python **3.10+**(标准库 `http.server`)+ PyMySQL + cryptography(Fernet)
- MySQL 客户端路径不硬编码:设置值 → PATH → 常见安装目录自动探测(env_probe.py);找不到仅降级提示,服务照常运行
- 密码: Fernet 对称加密,密钥存 `data/.secret.key`,配置文件中无明文
- 服务仅绑定 `127.0.0.1:8090`,不对外网开放
- 备份/还原任务互斥执行,单线程队列防止并发冲突
