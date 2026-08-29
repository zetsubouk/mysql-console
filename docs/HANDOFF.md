# HANDOFF — 项目交接文档

> 面向:接手本项目的开发者或 AI Agent。
> 最后更新:2026-08-29(自带运行时 A+B 方案:runtime_resolver 三级解析 + install/start/init 改造 + build_release 完整包/离线 wheels + 新增运行时单测;上一轮 2026-08-28:API 回归测试 + MC_DATA_DIR 重定位 + 登录锁定 500→503 修复 + PAT 清理 + 前端测试依赖固化(package.json/jsdom/npm test) + 离线单测 test_units.py + CI 三级流水线 + test_e2e 异步化 + 前端渐进模块化(目录索引/MCUtils/JSDoc)。
> 读完后建议按顺序看:README.md → DEVLOG.md(第七/八/二十三章)→ PLAN_v3.md → 本文档。

## 1. 一句话概述

单机 MySQL 可视化管理 Web 工具(Python 标准库 http.server + PyMySQL,零框架),
浏览器访问 `127.0.0.1:8090`,核心价值是**带实时进度的备份/还原 + 双引擎定时备份 + 登录认证 + 双后端存储**,
V3 改造后支持任意主机开箱部署、数据库可为本机或远程、首次运行有 Web 向导。

## 2. 当前状态(截至交接)

| 里程碑 | 状态 |
|---|---|
| V1 功能全量(监控/库表/用户进程/备份还原/定时) | ✅ 2026-08-23 单日完成 |
| V2 定时备份多任务化(内置+系统计划任务双引擎) | ✅ |
| V3-一期:跨平台自适配 + 首次运行三步向导 + 远程一等公民 | ✅ 已验证 |
| V3-二期:install.bat/.sh 一键安装 + systemd 服务化 + INSTALL.md | ✅ 已验证 |
| 认证+双后端(Phase1):轻量/全量存储 + 登录/token + 找回密码 | ✅ 2026-08-25 |
| 系统设置页(PhaseA):用户名/密码管理整合 | ✅ 2026-08-25 |
| 数据看板(PhaseB):健康评分/InnoDB/表空间/复制状态 | ✅ 2026-08-25 |
| 告警中心 / 服务器变量页 | ✅ 2026-08-26 阈值可配置(alert_max_conn/slow/running settings 键,前端回填+保存) |
| **GitHub 归档**:私有库 zetsubouk/mysql-console,单提交 main | ✅ 2026-08-25 |
| **一键初始化**(init.bat/.sh + cli_init.py,重置到全新首配状态) | ✅ 2026-08-27 |
| **服务器变量入口迁至数据看板下方 + 含义说明留空** | ✅ 2026-08-27 |
| **数据库管理**(MySQL 用户增删改授权 + 「数据库」页重启/状态检测) | ✅ 2026-08-27 |
| **软件自动更新**(检查 releases/定时+手动/下载校验备份/自更新重启) + 仓库转公开 v3.2.0 | ✅ 2026-08-27 |
| **API 回归测试** tests/test_api.py(隔离数据目录 + 核心路由链路 + 认证守卫 + 下载白名单) | ✅ 2026-08-28 |
| **MC_DATA_DIR 环境变量**:数据目录可重定位(测试隔离/可移植部署) | ✅ 2026-08-28 |
| **登录锁定状态 Bug 修复**:系统库不可达时登录原 500,现按约定 503「系统库不可用」 | ✅ 2026-08-28 |
| **清理 remote URL 明文 PAT**(凭据一律交凭据管理器,不写进 .git/config) | ✅ 2026-08-28 |
| **前端测试依赖固化**:package.json(jsdom ^26) + `npm test` 4 套 jsdom 回归(NODE_PATH 小债解除) | ✅ 2026-08-28 |
| **离线单元测试** tests/test_units.py(30 项,纯逻辑/mock,无需 MySQL/客户端) | ✅ 2026-08-28 |
| **CI 三级流水线** .github/workflows/ci.yml(后端矩阵 / 前端 jsdom / E2E MySQL 8 闭环) + test_e2e 异步化修正 | ✅ 2026-08-28 |
| **前端渐进模块化(P2)**:app.js 目录索引 + MCUtils 命名空间 + api/confirmDialog JSDoc + 切换全量模式确认收敛 confirmDialog(不做 ES Module) | ✅ 2026-08-28 |
| **用户授权修复与 root 保护**:bug1 编码路径解析修复(查看授权/设置权限/改密/删除全部恢复)+ root 授权禁止修改(前端拦截 + 后端 403 双端)+ 更新日志重复显示修复 + server 平台守卫(windll 仅 nt,修复 Linux 导入) | ✅ 2026-08-28 |
| **设置权限弹窗带出现有授权**:parseGrants(SHOW GRANTS→范围/库/权限/extra)+ loadCurrentGrantsIntoModal 回填(普通用户编辑即带出现状;USAGE 占位行跳过;界面外权限提示覆盖风险),新增 test_um_grants_prefill.js,6 套 jsdom 全绿 | ✅ 2026-08-28 |
| **定时备份全量模式字段丢失修复**:统一任务模型双后端打通(mc_schedule 新增 extra 列存 freq/time 等 + 旧 cron_expr 反解兼容);新建任务保存后默认启用;默认备份时间 02:00→00:00 | ✅ 2026-08-29 |
| **自带运行时 A+B**(无 Python 也能装):`src/runtime_resolver.py` 三级解析(内置 runtime→系统 Python 实测≥3.10→自动下载嵌入式,官方源+华为云/npmmirror 镜像);install.bat 交互确认(版本不满足先提示,**绝不改动用户系统 Python**);start/init 共享 `_resolve_python.bat`;系统 Python 缺依赖时拒绝 pip 安装改提示跑 install;`--runtime-zip` 本地包兜底;运行时缓存 runtime/resolved_python.txt | ✅ 2026-08-29(代码交付,**真机待用户手工测试**) |
| **build_release 双产物**:`--with-runtime` 产出 full-win64 完整包(嵌入式 Python + 预装 site-packages,全程离线);`--wheels-dir` 精简包附离线轮子;validate 同步扩展 | ✅ 2026-08-29(代码交付,未实际构建) |
| **运行时解析单测** tests/unit/test_runtime_resolver.py(26 项,纯标准库+mock,CI 已接入) | ✅ 2026-08-29 |
| 三期候选:可选访问口令(settings.access_token,非回环监听强制) | ⬜ 未立项 |
| **SQL 查询执行器**(后端 /api/query + 前端查询页:只读/限行/超时 Kill)——目前全项目无自定义 SQL 执行入口,核心缺口 | ⬜ 建议立项 |
| SSH 远程执行备份(本地免装 mysqldump) | 💡 已做可行性分析,用户未决策 |

## 2b. 目录规范化(2026-08-28 重构)

> 用户反馈:发布版根目录「源码与工程文件混放」,已按新布局重构。**模块间 import 零改动**,所有路径通过 `src/paths.py` 单一解析;入口/测试/CI 只需把模块搜索路径指向 `src/`(详情见 README 项目结构 与 DEVLOG §26)。

```
mysql-console/
├── src/              全部 Python 源码 + static/(前端)
├── docs/             INSTALL/RELEASE/MIGRATION/DEVLOG/HANDOFF/PLAN_v3/MANIFEST
├── scripts/          install|start|stop|init(.bat/.sh) + _resolve_python.bat + mysql-console.service + 构建脚本
├── tests/            api/ unit/ e2e/ frontend/ 分型
├── requirements.txt  README.md  LICENSE  pyproject.toml  package.json
└── data/             运行时数据(不入库;MC_DATA_DIR 可重定位)
    runtime/          自带独立运行时(嵌入式 Python + resolved_python.txt 缓存;不入库)
    wheels/           离线依赖轮子(仅构建时产出/随精简包发布;不入库)
```

关键变更对照(旧→新):
- `server.py` → `src/server.py`(入口不变:`python src/server.py`;`BASE_DIR` 语义改为 `APP_ROOT`=部署根)
- `version.py` → `src/version.py`(版本单一来源不变)
- `requirements.txt` 留在根(安装脚本 `-r "%ROOT%\requirements.txt"`)
- 测试:`python tests/api/test_api.py`、`python tests/unit/test_units.py`、`python tests/e2e/test_e2e.py`、npm test(tests/frontend/)
- 发布:`python scripts/build_release.py [--tag vX.Y.Z]` → `dist/mysql-console-X.Y.Z.zip/.tar.gz`(自动校验)
- 清单:`python scripts/regen_manifest.py` → `docs/MANIFEST.txt`

## 3. 技术栈与运行要求

- Python **≥3.10**(代码规避了 3.12+ 语法;原机 3.11 实测)
- 依赖仅 2 个:`pymysql>=1.1,<2`、`cryptography>=42`(装进项目 `.venv`)
- 前端:原生 JS + ECharts(本地文件 static/echarts.min.js),无构建步骤
- MySQL 客户端工具(mysqldump/mysql):仅备份/还原功能需要,**动态探测**(设置值→PATH→常见目录),不随包分发

## 4. 目录结构与关键文件

```
mysql-console/
├── server.py              # HTTP 服务+全部路由;Win32 原生对话框(ctypes);内置调度线程
├── backup_engine.py       # 备份/还原引擎(子进程流式管道+字节级进度);_cli_args 动态找客户端
├── env_probe.py           # [V3新增] MySQL 客户端三级探测;版本解析;/api/setup/env 数据源
├── config_store.py        # Fernet 加密连接配置;DEFAULT_SETTINGS 新键会自动补齐旧配置
├── mysql_client.py        # PyMySQL 查询封装(监控/库表/用户/进程)
├── schedule_store.py      # 定时任务存储与到点匹配(tm_wday 语义陷阱见 DEVLOG)
├── native_scheduler.py    # schtasks/systemd/cron 适配(_oncalendar 注意 Py<3.12 兼容写法)
├── cli_backup.py          # 计划任务命令行入口(--task <id>)
├── system_db.py           # [Phase1] 全量模式系统库管理(建库6表+StorageBackend 全 CRUD+旧文件迁移)
├── install.bat / install.sh   # 一键安装(.venv+依赖);install.sh 支持 --service/--print-service
├── start.bat / stop.bat   # Windows 启停(**纯 ASCII+CRLF,勿加中文**,原因见 §7)
├── start.sh / stop.sh     # Linux/macOS 启停
├── scripts/mysql-console.service  # systemd 模板(__BASE_DIR__/__USER__ 占位符由 install.sh 渲染)
├── static/{index.html,app.js,style.css,login.html}  # 单页应用;login=登录页;setup-modal=向导;settings-modal=服务设置
├── tests/                 # 回归:test_frontend.js / test_db_picker.js / test_um_preset_all.js / test_update_log.js(jsdom,npm test)
│                          #       test_api.py(HTTP 层) / test_units.py(离线单测) / test_e2e.py(异步备份还原) / test_progress*.py
├── package.json + package-lock.json  # 前端测试开发依赖(jsdom; node_modules/ 不入库,勿 push)
├── .github/workflows/ci.yml          # 三级 CI:后端矩阵 + 前端 npm test + E2E(MySQL 8 服务容器)
├── data/                  # 运行时数据(config.db 轻量存储 / .secret.key / backups / updates)——打包时剔除,勿 push;
│                          #   可用环境变量 MC_DATA_DIR 重定位(测试隔离/可移植部署)
└── HANDOFF.md / PLAN_v3.md / README.md / DEVLOG.md / MIGRATION.md / INSTALL.md
```

## 5. API 地图(全部挂在一个 BaseHTTPRequestHandler 上)

- 连接:`GET/POST /api/connections`、`PUT/DELETE /api/connections/<id>`、`POST /api/connect`(激活)、`POST /api/connections/test`
- 监控只读:`/api/overview` `/api/databases[/<name>]` `/api/users` `/api/processlist` `/api/monitor`
- 备份还原:`POST /api/backup`、`POST /api/restore`(返回 task_id)、`GET /api/task/<id>`(轮询进度)、
  `GET /api/backups`(历史)、`DELETE /api/backups/<id>`、`POST /api/dialog`(Win32 对话框)、`POST /api/browse`
- 定时:`GET/POST /api/schedules`、`PUT/DELETE /api/schedules/<id>`、`toggle/register/unregister`、`GET /api/schedules/env`
- 引导:[V3] `GET /api/setup/env`、`POST /api/setup/probe-client|test-db|finish`;设置:`GET/PUT /api/settings`
- 认证[Phase1 全量模式]:`POST /api/login|logout`、`GET /api/auth-status`、`POST /api/change-password|request-reset-code|reset-password|change-username`
- 看板[PhaseB]:`GET /api/dashboard/health|innodb|tablespace|replication`
- 告警/变量[进行中]:`GET /api/alerts`、`GET /api/variables`
- 服务/用户管理[2026-08-27]:`GET /api/service/status`、`POST /api/service/restart`、`POST /api/users`、`GET/PUT/DELETE /api/users/<u>@<h>`、`GET /api/users/<u>@<h>/grants`
- 自动更新[2026-08-27]:`GET /api/version`、`GET /api/update/check|badge|status`、`POST /api/update/prepare|apply`
- 模式切换[Phase1]:`POST /api/switch-to-full-mode`(轻量→全量,不可逆)

## 6. 验证方法(改动后必跑)

```bash
# ① 全模块编译(最快冒烟)
python -m py_compile src/*.py tests/api/*.py tests/unit/*.py tests/e2e/*.py

# ② 前端回归(依赖固化于 package.json,无需 NODE_PATH;fetch stub 返回 [] —— 新增顶层逻辑必须容错!)
npm install && npm test         # 6 套 jsdom 回归

# ②½ 离线测试:API 层回归 + 单元测试(隔离数据目录,无 MySQL/客户端也能跑;不触碰真实 data/)
python tests/api/test_api.py
python tests/unit/test_units.py      # 需 pymysql+cryptography(装进 .venv;无系统 pip 时
                                     #   python -m pip install --target _pydeps -r requirements.txt
                                     #   并设 PYTHONPATH=_pydeps 后运行)
python tests/unit/test_runtime_resolver.py   # 纯标准库,零依赖

# ③ 服务实启动(无 MySQL 的机器也能起,这是特性不是bug;无 Python 的机器先跑 install.bat)
start.bat 或 .venv\Scripts\python.exe src\server.py
curl http://127.0.0.1:8090/api/health            # {"ok": true}
curl http://127.0.0.1:8090/api/setup/env         # 如实报告环境缺什么

# ④ 有测试库时:e2e 备份还原闭环 / 大表进度平滑性
python tests/e2e/test_e2e.py && python tests/test_progress.py
```

## 7. 血泪陷阱清单(违反必翻车)

1. **`.bat` 必须纯 ASCII + CRLF**:含中文的 UTF-8 bat 即使 `chcp 65001` 也必炸——cmd 切代码页后按旧偏移读文件,命令从多字节字符中间剁碎;延时用 `ping -n 2 127.0.0.1 >nul` 不用 `timeout`(撞名 GNU coreutils);
2. **f-string 内嵌同类引号是 Python 3.12+ 语法**(PEP 701),本项目基线 3.10+,严禁;
3. **钉死不存在的依赖版本比不钉更糟**(曾因 `pymysql==2.2.8` 导致任何新机器装不上);
4. **Windows 商店 python 占位符会骗过 `command -v`**,shell 探测解释器必须先实际执行 `-c 'pass'` 验证;
5. ctypes 调 Win32 API 必须 `restype=c_void_p`,否则 64 位指针截断访问违规(server.py 对话框部分);
6. 删除 DOM 元素后必须全局 grep 其 ID(app.js 曾因此整页 JS 中断,R9);
7. jsdom 测试的 fetch stub 只返回 `[]`:前端新顶层代码对非对象响应必须容错;
8. 多进程端口共绑:Windows 下 SO_REUSEADDR 允许多进程绑同一端口,start.bat 已自动清理旧实例,改代码后重启务必确认旧进程已死;
9. MSYS/Git Bash 环境 `cmd //c` 会因路径转换失效,用 `cmd /c "echo.|script.bat"` 形式跑 bat 并喂掉 pause。
10. **`.gitignore` 只防未跟踪文件**:先提交过的敏感文件(如 `data/.secret.key`、`data/config.json`)必须显式 `git rm --cached`,否则仍会随历史推送;密钥类文件一旦进历史,须改写历史(压缩提交/过滤)才清除——上传公开仓库前必查 `git ls-files`。2026-08-25 曾因忽略此条而差点把 Fernet 密钥推上 GitHub。
11. **git remote URL 严禁内嵌 token**:`https://user:ghp_xxx@github.com/...` 形式的 remote 会把 PAT 明文留在 `.git/config`,任何能读该目录的进程都能窃取(2026-08-28 曾实际存在并已清理)。凭据一律交给 Git Credential Manager/凭据管理器,`git remote set-url` 保持无凭据 URL;推送前 `git remote get-url origin` 检查一次。
12. **bat 内 if/for 语句块中的 echo 文本严禁出现半角圆括号**:块以 `)` 结尾判定,echo 里的 `)`(如 "(offline)")会提前剁碎语句块——用 "offline mode" 之类措辞替代;整份 bat 保持纯 ASCII + CRLF(见第 1 条)。
13. **三级运行时解析策略必须三处同步**:`src/runtime_resolver.py`、`scripts/_resolve_python.bat`、install.bat 内的分支逻辑(顺序:内置 runtime → 系统 Python 实测 ≥3.10 → 下载嵌入式)。bat 在无 Python 时无法调用 Python 模块,故策略重复实现——改顺序时三处一起改。
14. **绝不向用户系统 Python 装任何包**:start/init.bat 检测到依赖缺失且只有系统 Python 可用时,一律提示跑 install.bat(它会建隔离 .venv 或下载私有 runtime),绝不直接 `pip install`(2026-08-29 起,保护客户开发环境)。

## 8. 待办与设计线索

- **自带运行时真机验证(最优先)**:本机无 Python 时跑 install.bat(交互确认→下载→离线装依赖→start.bat 起服务);有旧版 Python(如 3.9)时确认提示文案与"绝不影响系统环境"承诺;完整包(`--with-runtime`)离线安装链路;定时备份在私有 runtime 下真实注册与触发;
- **三期候选**:可选访问口令(settings.access_token,绑定非回环地址时强制)、备份文件浏览器下载接口;
- **SSH 远程执行备份**(可行性已论证):把 backup_engine 的子进程 stdout/stdin 换成 paramiko SSH 信道,
  进度/历史/定时全复用;需新增 SSH 凭据存储(Fernet 复用)+ host key 固定 + 向导第 2 步可跳过逻辑;
  版本不一致警告在该模式下天然消失(用服务器自己的 mysqldump);风险:SSH 凭据信任半径大,建议受限账号;
- **systemd 注册未在真实 Linux 验证**(开发机是 Windows):首次 Linux 部署先跑 `./install.sh --print-service` 审查;
- 小债:start.sh 的 stop.sh 依赖 lsof(极简容器可能没有,可换 ss/fuser 兜底);
  jsdom 测试的 NODE_PATH 外部注入:**已于 2026-08-28 解决**(package.json devDependencies 固化,`npm install && npm test`)。
- E2E 在 CI 由 MySQL 8 服务容器执行(本机不连真实 MySQL);test_e2e 已按 R7 异步接口重写(202+task_id 轮询)。

## 9. 给 AI Agent 的操作建议

- 本项目**有 git 且已有 GitHub 远程(私有 zetsubouk/mysql-console)**,重要改动前可先 `git commit` 打底;DEVLOG.md 是演进史,请延续"改动清单+验证记录+经验"格式追加;
- 敏感文件(`data/*`、`.venv/`、`__pycache__`)已被 `.gitignore` 覆盖并 `git rm --cached`,改动时**不要再 `git add` 它们**;
- 改后端先跑 §6-①③,改前端必跑 §6-②(它专抓"顶层引用不存在元素导致整页死"这类事故);
- 改 install/start/init/_resolve_python bat 或 runtime_resolver.py 时,重读 §7 第 12/13/14 条(块内括号/三处同步/不碰系统 Python);
- 新增 settings 键一律加进 `config_store.DEFAULT_SETTINGS`(旧配置自动补齐机制已就位);
- 涉及路径/编码的改动,在**真实 cmd 窗口**和 PowerShell 各验一遍,别信单一终端的表现;
- 用户偏好:中文交流、手动触发不做定时任务、方案要先讲清"为什么"、文件产物放规范目录。
- 前端回归命令已固化:`npm install && npm test`(无需再设 NODE_PATH);改前端必跑;后端改完跑 §6 ①③ + ②½(api/units)。
