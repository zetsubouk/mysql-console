# HANDOFF — 项目交接文档

> 面向:接手本项目的开发者或 AI Agent。
> 最后更新:2026-08-25(认证+双后端+系统设置+数据看板完成后,已上传 GitHub)。
> 读完后建议按顺序看:README.md → DEVLOG.md(第七章 V3 / 第八章认证双后端看板)→ PLAN_v3.md → 本文档。

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
| 三期候选:可选访问口令、备份文件浏览器下载 | ⬜ 未立项 |
| SSH 远程执行备份(本地免装 mysqldump) | 💡 已做可行性分析,用户未决策 |

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
├── tests/                 # test_frontend.js(jsdom 回归) / test_e2e.py / test_progress*.py
├── data/                  # 运行时数据(config.json 加密密码 / .secret.key / 历史 / 日志 / backups)——打包时剔除,勿 push
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
python -m py_compile *.py

# ② 前端回归(需 jsdom;fetch stub 返回 [] —— 新增顶层逻辑必须容错!)
NODE_PATH=<jsdom所在node_modules> node tests/test_frontend.js     # 期望 10/10 OK

# ③ 服务实启动(无 MySQL 的机器也能起,这是特性不是 bug)
start.bat 或 .venv\Scripts\python.exe server.py
curl http://127.0.0.1:8090/api/health            # {"ok": true}
curl http://127.0.0.1:8090/api/setup/env         # 如实报告环境缺什么

# ④ 有测试库时:e2e 备份还原闭环 / 大表进度平滑性
python tests/test_e2e.py && python tests/test_progress.py
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

## 8. 待办与设计线索

- **三期候选**:可选访问口令(settings.access_token,绑定非回环地址时强制)、备份文件浏览器下载接口;
- **SSH 远程执行备份**(可行性已论证):把 backup_engine 的子进程 stdout/stdin 换成 paramiko SSH 信道,
  进度/历史/定时全复用;需新增 SSH 凭据存储(Fernet 复用)+ host key 固定 + 向导第 2 步可跳过逻辑;
  版本不一致警告在该模式下天然消失(用服务器自己的 mysqldump);风险:SSH 凭据信任半径大,建议受限账号;
- **systemd 注册未在真实 Linux 验证**(开发机是 Windows):首次 Linux 部署先跑 `./install.sh --print-service` 审查;
- 小债:start.sh 的 stop.sh 依赖 lsof(极简容器可能没有,可换 ss/fuser 兜底);
  jsdom 测试的 NODE_PATH 需要外部注入(可考虑 package.json devDependencies 固化)。

## 9. 给 AI Agent 的操作建议

- 本项目**有 git 且已有 GitHub 远程(私有 zetsubouk/mysql-console)**,重要改动前可先 `git commit` 打底;DEVLOG.md 是演进史,请延续"改动清单+验证记录+经验"格式追加;
- 敏感文件(`data/*`、`.venv/`、`__pycache__`)已被 `.gitignore` 覆盖并 `git rm --cached`,改动时**不要再 `git add` 它们**;
- 改后端先跑 §6-①③,改前端必跑 §6-②(它专抓"顶层引用不存在元素导致整页死"这类事故);
- 新增 settings 键一律加进 `config_store.DEFAULT_SETTINGS`(旧配置自动补齐机制已就位);
- 涉及路径/编码的改动,在**真实 cmd 窗口**和 PowerShell 各验一遍,别信单一终端的表现;
- 用户偏好:中文交流、手动触发不做定时任务、方案要先讲清"为什么"、文件产物放规范目录。
