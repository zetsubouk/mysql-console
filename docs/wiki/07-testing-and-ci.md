# 07 · 测试体系与 CI 流水线

## 一、测试分型总览

`tests/` 实际含 **5 个子目录**（unit / api / e2e / frontend / vitest）。全部离线测试通过 `MC_DATA_DIR` 隔离数据目录，**不触碰真实 `data/`**。

| 类型 | 目录 | 覆盖内容 | 运行命令 | 依赖 |
|---|---|---|---|---|
| 离线单元 | [tests/unit/](../../tests/unit/) | `test_units.py`（1407 行：backup_engine 白名单/gzip ISIZE、env_probe、schedule_store 周期、local_store CRUD、config_store Fernet/PBKDF2、mysql_client mock、system_db/ai_client/updater/security）；`test_runtime_resolver.py`（三级解析优先级/._pth 解注/防穿越解压/缓存）；`test_pip_bootstrap.py`（PEP 503 解析/轮子挑选）；`test_native_script.py`（.ps1/.sh 生成，BOM/CRLF/转义） | `python tests/unit/test_units.py`（其余同型直跑） | unit 全套需 pymysql+cryptography；resolver/pip_bootstrap/native_script 纯标准库零依赖 |
| API 回归 | [tests/api/test_api.py](../../tests/api/test_api.py) | 临时 `MC_DATA_DIR` + 进程内 ThreadingHTTPServer（端口 0）：核心路由链路、**备份下载白名单（防任意文件读取）**、降级链路（无连接备份→400）、全量模式认证守卫（monkeypatch 401/login 503）；刻意不测 update/service/dialog（真网络/真重启） | `python tests/api/test_api.py` | 无 MySQL 也可跑 |
| E2E | [tests/e2e/](../../tests/e2e/) | `test_e2e.py`：建库 → **异步**备份（202 + task_id 轮询）→ 清空 → 异步还原 → 行数校验；`test_progress*.py` 进度平滑性 | `python tests/e2e/test_e2e.py` | 需真实 MySQL + 服务运行（CI 用 mysql:8.0 容器） |
| 前端 jsdom | [tests/frontend/](../../tests/frontend/) | `test_frontend.js`（app.js 顶层执行 + 导航 + 页面切换）、`test_db_picker`、用户管理四件套（preset_all / root_guard / grants_prefill）、`test_update_log` | `npm test`（或单项 `npm run test:frontend` 等） | `npm ci`（jsdom 固化于 package.json） |
| 前端 vitest | [tests/vitest/](../../tests/vitest/) | `dashboard.test.js`（dashboard-helpers 6 纯函数：降采样/联动过滤/状态判定）、`dashboard-interaction.test.js` | `npm run test:vitest` | 同上 |

## 二、改动后验证清单（项目约定）

```bash
# ① 全模块编译（最快冒烟）
python -m py_compile src/*.py tests/api/*.py tests/unit/*.py tests/e2e/*.py

# ② 前端回归（改前端必跑；专抓"顶层引用不存在元素导致整页死"）
npm install && npm test

# ③ 离线后端回归
python tests/api/test_api.py
python tests/unit/test_units.py

# ④ 服务实启动（无 MySQL 也能起——这是特性不是 bug）
#    Windows: start.bat ；Linux: ./start.sh 或 .venv/bin/python src/server.py
curl http://127.0.0.1:8090/api/health          # {"ok": true}
curl http://127.0.0.1:8090/api/setup/env

# ⑤ 有测试库时：E2E 备份还原闭环
python tests/e2e/test_e2e.py
```

## 三、CI 流水线（[.github/workflows/ci.yml](../../.github/workflows/ci.yml)）

> 头部注释声明"三级"，实际 **5 个 job**（crossplatform 与 systemd 为注释未提及的扩展层）。触发：push main + 所有 PR。

| # | job | 环境 | 内容 |
|---|---|---|---|
| ① | `backend` | ubuntu，Python **3.10/3.11/3.12 矩阵** | pip 装依赖 → `compileall` 编译冒烟 → api 回归 → units → 3 个纯标准库单测 → **4 包构建校验**（fake tools 桩目录避免真下载；slim-linux / slim-win64 / standard-linux 三条构建命令） |
| ② | `crossplatform` | windows-latest + macos-latest，Python 3.11 | 编译 + api 回归 + 单测 → **真机启动冒烟**（隔离 MC_DATA_DIR 后台拉起服务，30s 内轮询 /api/health） |
| ③ | `frontend` | Node 20 | `npm ci` → `npm test`（6 个 jsdom + vitest） |
| ④ | `e2e` | ubuntu + **service 容器 mysql:8.0**（root/root） | apt 装 mysql-client-8.0（GTID 参数需真 8.x mysqldump，MariaDB 客户端不支持）→ 内联 Python 建隔离数据目录 + 保存激活连接 → 同 step 内启动服务 → `test_e2e.py` + `test_progress.py` |
| ⑤ | `systemd` | ubuntu **systemd 容器**（`--privileged --cgroupns=host`） | `install.sh --print-service` 审查 unit 渲染 → `--service` 注册启动 → `systemctl is-active/is-enabled` → 健康检查 → **restart 再验**（Restart=on-failure）→ `--remove-service` 断言无残留 |

## 四、前端测试两条硬规范

1. **jsdom 的 fetch stub 只返回 `[]`**：前端新增顶层逻辑必须对非对象响应容错，否则测试即炸（也是真实弱网防御）。
2. **删除 DOM 元素后全局 grep 其 ID**：app.js 曾因残留引用整页 JS 中断（DEVLOG R9）；测试套件专抓此类事故。

## 五、避坑清单（源自 [HANDOFF.md §7](../HANDOFF.md)，违反必翻车）

| # | 陷阱 | 规则 |
|---|---|---|
| 1 | bat 编码 | `.bat` 必须**纯 ASCII + CRLF**；块内 echo 禁半角圆括号；延时用 `ping -n 2` 不用 `timeout` |
| 2 | 语法基线 | f-string 内嵌同类引号是 Python 3.12+（PEP 701）语法，基线 3.10 **严禁** |
| 3 | 依赖版本 | 钉死不存在的版本比不钉更糟（曾 `pymysql==2.2.8` 致全平台装不上） |
| 4 | 商店占位符 | Windows 商店 python 骗过 `command -v`；探测解释器必须**实际执行** `-c 'pass'` |
| 5 | ctypes | Win32 API 必须 `restype=c_void_p`，否则 64 位指针截断崩溃 |
| 6 | sys.path | 嵌入式 Python（._pth）无 sys.path[0] 且忽略 PYTHONPATH；入口脚本必须显式 `sys.path.insert(0, 脚本目录)`，脚本路径去 `..` |
| 7 | 系统环境 | **绝不向用户系统 Python 装包**；依赖缺失一律提示跑 install.bat（建 .venv 或私有 runtime） |
| 8 | bat shift | `shift` 会连 `%0` 移动；循环前先 `set "SCRIPTDIR=%~dp0"` 固化 |
| 9 | 下载超时 | curl 必带 `--connect-timeout 10 --max-time 180`；PowerShell `-TimeoutSec 120`（防被墙源卡死） |
| 10 | 端口共绑 | Windows SO_REUSEADDR 允许多进程绑同端口；改代码重启务必确认旧进程已死（start.bat 已自动清理） |
| 11 | 敏感文件 | `.gitignore` 只防未跟踪文件；曾提交过的敏感文件须 `git rm --cached`；remote URL 严禁内嵌 token |
| 12 | MSYS | Git Bash 下 bat 用 `cmd /c "echo.\|script.bat"` 形式跑并喂掉 pause |
| 13 | 策略同步 | 三级运行时解析策略三处（runtime_resolver.py / _resolve_python.bat / install.bat）同步修改 |
| 14 | 新增设置键 | 一律加进 `config_store.DEFAULT_SETTINGS`（自动补齐机制依赖） |
