# MySQL Console - 数据库可视化管理平台

基于 Python 3.10+ 标准库 + PyMySQL 的本地 MySQL 管理 Web 应用,通过浏览器图形化管理 MySQL 数据库:系统状态监控、数据库统计、用户/连接管理、**备份与还原**、定时备份、登录认证等。**本机无需安装 MySQL**——被管理的库可以是本机实例或任意网络可达的独立服务器。

功能总览、备份还原说明、定时备份、技术要点见 **docs/INSTALL.md** 与 **docs/README-full.md** 前的链接目录;开发背景与历史记录见 **docs/DEVLOG.md**。

## 快速启动

> 详细安装部署指南(双平台、开机自启、systemd、远程库配置、FAQ)见 **docs/INSTALL.md**。

### Windows
1. 双击 **`install.bat`**(Windows)/ **`./install.sh`**(Linux/macOS):首次建 `.venv` 并装依赖;
2. 双击 **`start.bat`** / 运行 **`./start.sh`**:服务启动后浏览器访问 `http://127.0.0.1:8090`;
3. 停止:`stop.bat` / `stop.sh`;恢复出厂:`init.bat` / `init.sh`(会删除全部配置、系统库与备份)。
4. Linux 生产推荐:`sudo ./install.sh --service` 注册 systemd 开机自启。

> 以上脚本位于 `scripts/`(开发仓库)或在发布包根目录(发布包已把启动/安装脚本复制到包根)。两者均可直接运行。

## 项目结构

```
mysql-console/
├── src/                          # ★ 全部源码(此前平铺在根目录)
│   ├── paths.py                  # 路径单一来源:APP_ROOT / DATA_DIR / STATIC_DIR
│   ├── server.py                 # HTTP 服务入口(API + 静态资源 + 定时调度 + 原生对话框)
│   ├── cli_init.py / cli_backup.py
│   ├── config_store.py / local_store.py / mysql_client.py / backup_engine.py
│   ├── env_probe.py / schedule_store.py / native_scheduler.py
│   ├── system_db.py / service_manager.py / sys_resources.py / variable_docs.py
│   ├── updater.py / version.py
│   └── static/                   # 前端:index.html / app.js / style.css / login.html / echarts.min.js
├── docs/                         # ★ 文档集中
│   ├── INSTALL.md / RELEASE.md / MIGRATION.md / DEVLOG.md / HANDOFF.md / PLAN_v3.md / MANIFEST.txt
├── scripts/                      # ★ 部署与开发脚本
│   ├── install.bat/.sh  start.bat/.sh  stop.bat/.sh  init.bat/.sh
│   ├── mysql-console.service     # systemd 模板
│   └── _kill8090.ps1 / _kill_all_server.ps1 / build_release.py / regen_manifest.py
├── tests/                        # ★ 按类型分型
│   ├── api/   (test_api.py)      # API 层回归(隔离数据目录,无 MySQL 可跑)
│   ├── unit/  (test_units.py)    # 离线单元测试(纯逻辑/mock)
│   ├── e2e/   (备份→还原闭环,需 MySQL)   ├── frontend/ (jsdom 回归)
├── requirements.txt / pyproject.toml / package.json
├── LICENSE                       # MIT(新增)
├── data/                         # 运行时数据(不入库;可用 MC_DATA_DIR 重定位)
└── .github/workflows/ci.yml      # 三级 CI(路径已适配 src/)
```

## 测试

```bash
npm ci && npm test                # 前端 jsdom 回归(6 套,路径已适配 src/static)
python tests/api/test_api.py      # API 层回归(隔离数据目录,无需 MySQL)
python tests/unit/test_units.py   # 离线单元测试
python tests/e2e/test_e2e.py      # 备份→还原端到端(需运行中的服务 + MySQL 客户端)
```

以上全部由 `.github/workflows/ci.yml` 在 GitHub 自动执行。

## 发布打包

```bash
python scripts/regen_manifest.py    # 重新生成 docs/MANIFEST.txt(git 已跟踪清单)
python scripts/build_release.py     # 一键产出 dist/mysql-console-X.Y.Z.zip/.tar.gz(自动校验)
pip install .                       # 可选:pip 安装后可用 mysql-console 命令启动
```

详细发版流程(脱敏扫描、tag、上传资产、回读校验)见 **docs/RELEASE.md**。