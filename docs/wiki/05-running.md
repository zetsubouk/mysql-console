# 05 · 项目运行方式

## 1. 环境要求

| 项 | 要求 |
|---|---|
| Python | ≥3.10（本机没有也可装，见 §2 三级运行时） |
| 运行依赖 | 仅 `pymysql>=1.1,<2` + `cryptography>=42`（装进项目 `.venv`，**绝不装进系统 Python**） |
| 前端 | 无需构建；浏览器访问即用 |
| MySQL | 部署机**无需安装**；被管库本机/远程皆可 |
| mysqldump/mysql | 仅备份/还原需要，动态探测或向导下载 |

## 2. 安装与启动

### 2.1 开发仓库（源码直跑）

```bash
# Linux / macOS
./platforms/linux/scripts/install.sh    # 建 .venv + 装依赖
./platforms/linux/scripts/start.sh      # 启动，默认 http://127.0.0.1:8090
# 或直接: .venv/bin/python src/server.py

# Windows
platforms\win64\scripts\install.bat
platforms\win64\scripts\start.bat
```

### 2.2 发布包（解压即用）

按 4 包矩阵任选其一：`mysql-console-<ver>-win64.zip` / `-linux.tar.gz`（standard，内置双版本 MySQL 客户端）或 `-slim-*`（约 600K，向导提示下载/跳过客户端）。发布包根目录只含 `install` 启动器，start/stop/init 在初始化完成后自动生成。

### 2.3 三级运行时解析（无 Python 也能装）

```
① 内置 runtime/python/（完整包内置或历史下载）
        ↓ 无
② 系统 Python 真实执行探测（py -3 / python / python3，须 ≥3.10，防商店占位符骗过）
        ↓ 无/不满足
③ 下载嵌入式 Python 3.12.10 到 runtime/python（python.org → 华为云 → npmmirror，带超时）
```

原则：**绝不改动用户系统 Python/PATH/注册表**；删 `runtime/` 即彻底移除。策略在 `runtime_resolver.py`、`_resolve_python.bat`、`install.bat` 三处重复实现，改动需同步。

### 2.4 pip 安装方式（可选）

```bash
pip install .
mysql-console    # 启动服务
```

> 静态资源以数据文件安装到 `<sys.prefix>/share/mysql-console/static`，`paths.static_dir()` 兜底读取。注意 pyproject 的 py-modules 清单可能缺 5 个模块（见 [04-dependencies §3.1](04-dependencies.md#31-运行时依赖最小集)）。

## 3. 首次运行三步向导

浏览器打开 `http://127.0.0.1:8090` → 登录（首次未设密码则免认证）→ 向导：

1. **环境检测**：`GET /api/setup/env` 如实报告 Python/pymysql/cryptography/mysqldump/mysql 状态；
2. **MySQL 客户端目录**：slim 版提供下载（4 源逐试 + SHA256 校验）/跳过；standard 静默跳过；
3. **数据库连接**：测试连接（可经 SSH）→ 完成（lite 直接就绪；full 会建系统库 `_mysql_console`）。

## 4. 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `MC_DATA_DIR` | `<部署根>/data/` | 数据目录重定位（测试隔离/便携部署）；pip 形态兜底 `~/.mysql-console` |
| `MC_PORT` | `8090` | 监听端口 |
| `MC_HOST` | `127.0.0.1` | 监听地址；**绑非回环必须配 MC_ACCESS_TOKEN，否则拒绝启动** |
| `MC_ACCESS_TOKEN` | —（settings 可设，Fernet 加密落库） | 访问令牌，请求头 `X-Access-Token`；环境变量优先 |
| `MC_TLS` | `0` | `1` 启用 TLS（自签证书生成于 `data/tls/`，`MC_CERT`/`MC_KEY` 可指定外部证书） |
| `MC_PYTHON` | —（Linux install.sh） | 显式指定 Python 解释器 |
| `PYTHONUTF8` | start.bat 设 1 | Windows 启动强制 UTF-8 |

## 5. 生产部署（Linux systemd）

```bash
sudo ./platforms/linux/scripts/install.sh --service      # 渲染 unit + enable --now
./platforms/linux/scripts/install.sh --print-service     # 只打印 unit 供审查（首次部署推荐先审查）
./platforms/linux/scripts/install.sh --remove-service    # 注销
```

unit 模板：`Type=simple`、`Restart=on-failure`（5s）、`ExecStart=.venv/bin/python src/server.py`，`__BASE_DIR__`/`__USER__` 占位符由 install.sh 渲染。Windows 平台定时能力用 schtasks 承载（服务化无对应方案）。

## 6. 日常验证

```bash
curl http://127.0.0.1:8090/api/health     # {"ok": true}
curl http://127.0.0.1:8090/api/setup/env  # 环境自检（如实报告缺什么）
```

## 7. 测试（改动后必跑）

```bash
npm ci && npm test                              # 前端 6 套 jsdom + vitest
python tests/api/test_api.py                    # API 层回归（隔离数据目录，无 MySQL）
python tests/unit/test_units.py                 # 离线单元测试（30+ 项）
python tests/unit/test_runtime_resolver.py      # 运行时解析（纯标准库零依赖）
python tests/unit/test_pip_bootstrap.py
python tests/unit/test_native_script.py
python -m py_compile src/*.py                   # 全模块编译冒烟（最快）
python tests/e2e/test_e2e.py                    # 备份→还原 E2E（需真实 MySQL + 服务运行）
```

分层细节见 [07-testing-and-ci.md](07-testing-and-ci.md)。

## 8. 构建发布

```bash
python scripts/build_release.py --platform win64 --variant standard   # 产出 dist/*.zip
python scripts/build_release.py --platform linux --variant slim       # 产出 dist/*.tar.gz
# 可选参数:
#   --with-runtime   仅 standard-win64：内置嵌入式 Python + 预装依赖（离线完整包）
#   --tools-dir <bin> 附上本地 MySQL 客户端目录（生成 SHA256SUMS 清单）
#   --wheels-dir <d>  slim 包附离线依赖轮子
#   --tag vX.Y.Z      版本号（缺省读 src/version.py）
#   --runtime-zip <f> 本地运行时包离线构建
```

流程：文件收集（`git ls-files` 白名单 + 仅含 install 启动器）→ 可选装配（runtime/tools/wheels）→ 打包 → **validate**（必需白名单 + 禁入黑名单：tests/.github/data/.venv/node_modules 等）→ 打印大小与 sha256。

发版配套：

```bash
python scripts/sync_version.py --set 3.8.2   # version.py → pyproject/package.json/README 徽章
# 注意: ci.yml 中 --tag 硬编码版本需手动同步
python scripts/regen_manifest.py             # 重写 docs/MANIFEST.txt
```

## 9. 初始化 / 恢复出厂（破坏性操作）

```bash
./platforms/linux/scripts/init.sh     # 或 platforms\win64\scripts\init.bat
# 流程: 杀 8090 实例 → cli_init.py --check 预览将删数据 → 交互输入 y 确认 → --do --force
```

删除范围：全部配置（config.db / .secret.key / 旧 config.json）、日志、备份文件；全量模式额外 **DROP 系统配置库 `_mysql_console`**。绝不触碰业务库，程序文件保留。

## 10. 软件自更新

页面触发或按 `update_check_interval`（hourly/daily/weekly/off）自动检查：

```
check（GitHub API → 多级降级缓存）→ prepare（下载 .part + 大小/SHA256 校验
→ 解压归一 → 当前代码备份到 data/updates/backup/<ver>/）→ apply（独立进程
apply_update.py：等 8090 释放 → 删旧拷新[保留 data/.venv 等] → 按原方式重启）
```

更新**只替换代码，绝不碰 `data/`**；进度与日志见 `data/updates/update.log`（`GET /api/update/status` 尾部 50 行）。

## 11. 数据目录布局（运行时生成，不入库）

```
data/
├── config.db(+wal/shm)   # lite 模式 SQLite（meta/connections/settings）
├── .secret.key           # Fernet 密钥（首次自动生成，勿泄勿删）
├── backups/              # 本地备份（remote 备份直写远端不落此处）
├── alerts_history.json / health_history.json   # metrics 采样
├── updates/              # 自更新工作区（staging/backup/apply_update.py/update.log）
├── tls/                  # 自签证书（MC_TLS=1 时）
└── logs/                 # 运行日志
runtime/
├── python/               # 三级解析的独立运行时（嵌入式 Python）
└── resolved_python.txt   # 解析结果缓存（启动脚本兜底）
tools/
└── mysql-5.7/ mysql-8.0/ # 内置 MySQL 客户端（发布包或向导下载落位）
```
