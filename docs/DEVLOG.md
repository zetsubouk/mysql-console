# MySQL Console 开发记录(DEVLOG)

> 首个开发周期:2026-08-23(单日完成)。本文记录功能演进、Bug 修复、技术决策与经验,供后续迭代参考。

## 一、项目概览

本地 MySQL 可视化管理 Web 应用,浏览器访问 `http://127.0.0.1:8090`。
核心:状态监控、数据库详情、用户/连接管理、**备份/还原(带实时进度)**、定时备份、原生文件对话框。

**技术栈**: Python 3.13 标准库(`http.server`) + PyMySQL(唯一第三方依赖)+ 原生 JS + ECharts 5.5.1(本地引入,1MB)
**备份引擎**: 本机 MySQL 客户端(mysqldump/mysql)官方 CLI(逻辑备份,`--single-transaction` 在线一致性)

## 二、功能演进时间线

| 时间 | 里程碑 |
|---|---|
| 08:26 | 需求确认:自研方案 + mysqldump 逻辑备份 + 网页输入凭据 + 定时备份 |
| 08:30 | M1 骨架完成:HTTP 服务、加密连接配置、连接测试 |
| 08:32 | M2/M3/M4 完成:监控仪表盘、数据库详情、用户/连接、备份还原、定时备份、日志 |
| 08:35 | 修复前端全灭 bug(顶层 null 引用) |
| 08:43 | 修复 3 个功能 bug(SQL 列名、目录浏览路径、下拉空白) |
| 08:53 | 修复 SQL 列名(TABLE_SCHEMA)、连接激活状态显示与持久化 |
| 09:02 | 异步任务进度弹窗 + 原生文件对话框(第一版 PowerShell 方案) |
| 09:23 | 原生对话框改 ctypes 方案(PowerShell 不可靠) |
| 09:29 | 修复 ctypes 64 位指针截断崩溃 |
| 09:34 | 修复进度轮询误报错误 + 关闭按钮无反应 |
| 09:41 | 整理归档(代码/文档/测试归入 tests/,生成 zip) |
| 16:20 | R7:备份进度卡首表不滚动 + 完成无关闭按钮(重构进度引擎) |
| 17:10 | R8:原生对话框被浏览器遮挡 + 进度弹窗详情区无效/完成交互优化 |
| 18:20 | V2:定时备份多任务化(OS 自动识别 + 内置/系统计划任务双引擎) |
| 18:35 | V2 收尾:native_registered 持久化 + 引擎切换自动反注册 + schtasks 真实链路验证 |
| 08-24 | V3:跨平台部署改造一期(环境自适配/三步向导/远程库) + 二期(install.bat/.sh + systemd) |
| 08-25 凌晨 | Phase1:双后端存储(lite/full) + 登录认证体系(见第八章) |
| 08-25 上午 | Phase1 补充(找回密码/步态选择) + PhaseA(系统设置页) + PhaseB(数据看板) |
| 08-25 上传 | 压缩历史为单提交并推送到 GitHub 私有库 zetsubouk/mysql-console(见第九章) |
| 08-26 | 告警阈值可配置化(DEFAULT_SETTINGS 新增 alert_max_conn/slow/running;alerts() 改为参数接收;前端回填+保存真实化) |
| 08-27 | 数据库管理:MySQL 用户增删改授权(权限模板)+「数据库」页重启/状态检测(见第十七章) |
| 08-27 | 系统自动更新+仓库转公开(见第十八章) |
| 08-27 | v3.3.0 正式发布(公开库,含自动更新+版本统一) |
| 08-28 | 本地 v3.3.0 实测升级检测失效,定位 compare 符号反;经完整自更新链路升到 v3.4.0(见第二十一章) |
| 08-28 | v3.4.1 发布:修复 compare 符号写反导致的升级检测恒失效(自 v3.2.0 起传播),首修升级 bug 版本 |
| 08-28 | v3.4.2 发布:用户管理「完整权限」模板修复 + 软件更新面板无条件展示最新版本更新日志(见第二十二章) |
| 08-28 | v3.5.0 发布:目录结构化重构(src/ docs/ scripts/ tests/ 分型,解决源码与工程文件混放)+ 自动发布打包(build_release.py/regen_manifest.py/pyproject.toml/License)+ .bat 纯 ASCII+CRLF 修复(见第二十六章) |

## 二点五、V2 定时备份重构(18:20)

**需求**: cron 表达式对用户不友好;单任务限制;需要任务列表界面;支持 OS 自适应的系统计划任务与内置调度器二选一。

### 新增模块
- `schedule_store.py`: 多任务存储(`data/schedule_tasks.json`),字段含 `engine`(builtin/native)、`freq`(hourly/daily/weekly/monthly/once)、`is_due()` 到点匹配、`describe()` 人性化周期描述、旧 `schedule_*` 单任务配置自动迁移(迁移后关闭旧开关防双跑)。
- `native_scheduler.py`: OS 自动识别(win32/darwin/linux + systemd/cron 探测);Windows 用 `schtasks /create /tn MySQLConsole_<id>`,Linux 优先 systemd timer(Persistent=true)次选 crontab(带 `#mysqlconsole:<id>` 标记行,便于幂等清理);提供 register/unregister/status/gen_command。
- `cli_backup.py`: 命令行执行入口(`--task <id>` / `--list`),供系统计划任务调用,执行后同样回写 last_run/last_result 并按任务 keep 清理。

### API
- `GET /api/schedules` 列表(附 desc)、`POST /api/schedules` 新建、`PUT/DELETE /api/schedules/<id>`、
- `POST /api/schedules/toggle` 启停、`POST /api/schedules/register|unregister` 注册到系统、
- `GET /api/schedules/env` 返回 `{os, native_engine, native_available, python_path}` 供前端动态渲染。
- 原 `/api/schedule` 保留兼容。旧 `_cron_match` 已删除。

### 前端
- 「定时备份」页改为任务列表表格(名称/周期/范围/调度方式徽章/状态/上次执行结果)+ 新建/编辑弹窗表单(周期下拉联动字段、调度方式单选、库多选、保留份数)。
- 环境提示条显示当前 OS 与可用系统调度方式;native 任务保存时自动注册并 toast 结果。

### 经验
1. **tm_wday 语义陷阱**: Python `struct_time.tm_wday` 是 0=周一,而业务定义 0=周日,需 `(tm_wday+1)%7` 转换——测试驱动发现。
2. **单测 is_due 需要 check_enabled 开关**,否则构造的任务字典必须 enabled=true 才能测匹配逻辑。
3. **schtasks 周期映射**: hourly→`/sc hourly /mo N`;weekly→`/d SUN..SAT`(注意 schtasks 的星期缩写是英文周日=Sun,与 tm_wday 无关)。
4. **crontab 幂等写法**: 每条自动任务带唯一标记注释,写入前先过滤旧行,避免重复注册。
5. **旧配置迁移要关旧开关**: 迁移成新任务后立即 `save_settings({"schedule_enabled": False})`,防止新旧调度器同一任务双跑。
6. **引擎切换一致性**: PUT 更新任务时检测 engine 从 native→builtin 且已注册,自动调 unregister 并复位 `native_registered` 标志;register/unregister 接口成功后同步持久化该标志;删除任务前也先反注册。已通过 schtasks 真实注册/查询/反注册链路验证。
7. **schtasks 输出编码**: schtasks 在 GBK 控制台输出中文为乱码,但 returncode 可靠,判断注册状态用 `schtasks /query /tn <名>` 的退出码即可。

## 三、Bug 修复记录(共 10 轮)

### R1 点击任何功能无反应(08:35)
- **根因**: `app.js` 顶层 `$("#bk-scope").onchange = null;` 引用了不存在的元素 ID,`null.onchange` 抛 TypeError,整个脚本中断,所有事件绑定失效。
- **修复**: 删除该行;内联 onclick 传参统一改为 `data-*` 属性 + 全局函数(避免路径含引号破坏 HTML 属性)。
- **经验**: 前端全局脚本中任何 `$("#不存在id")` 顶层操作都会让整页 JS 崩溃。

### R2 三个功能 bug(08:43)
- **数据库列表报错**: `database_list` SQL 引用了 `information_schema.tables` 不存在的列 `schema_charset_name`(该信息在 `schemata` 表)。→ JOIN schemata 取 `DEFAULT_CHARACTER_SET_NAME`。
- **备份路径 C/D 盘错乱**: 后端根目录 dirs 返回纯字符串,前端拼接 `d.path + "\\" + x` 产生 `\C:\` 假路径。→ 后端统一返回 `{name, path}` 完整路径对象。
- **下拉空白无提示**: → 失败时显示"请先在连接管理激活连接"提示。
- **隐藏问题**: 6 个 server.py 进程同时监听 8090(Windows SO_REUSEADDR 允许多进程绑定),请求分发到旧代码 → "改了不生效"。→ start.bat 启动前自动清理旧实例。

### R3 SQL 列名再错(08:53)
- **根因**: R2 修复把列名写成 `t.schema_name`,但 `information_schema.tables` 的列是 **`TABLE_SCHEMA`**(`schema_name` 是 schemata 表的列)。
- **修复**: `t.TABLE_SCHEMA` + `s.SCHEMA_NAME`。
- **新增**: 连接管理"激活状态"列 + 激活持久化(`config.json` 存 `active_conn_id`,服务重启自动恢复)。

### R4 原生对话框无反应(09:23)
- **根因**: 第一版用 PowerShell 子进程弹窗,存在引号转义与权限问题(开发环境直接拦截 PowerShell 调用)。
- **修复**: 弃用 PowerShell,改 **ctypes 直接调 Win32 API**: `GetOpenFileNameW`(文件)、`SHBrowseForFolderW`(目录)。对话框必须在 STA 线程调用。

### R5 选择目录后访问违规崩溃(09:29)
- **根因**: ctypes 调用 `SHBrowseForFolderW` 未设置 `restype`,默认按 32 位 `c_int` 处理返回值,**64 位 PIDL 指针被截断**,选中目录后解析句柄 → 读无效内存崩溃。
- **修复**: 所有 Win32 API 显式声明 `argtypes`/`restype`(`_SHBrowseForFolderW.restype = c_void_p` 等)。
- **验证**: 用 `SHGetSpecialFolderLocation` 获取真实 PIDL → 解析路径成功。

### R6 备份进度误报 + 关闭按钮无反应(09:34)
- **根因1**: mysqldump `--verbose` 的正常日志(`-- Sending SELECT query...`)被误收为错误信息;前端 `api()` 把任何带 `error` 字段的响应当错误抛 → 误报"查询进度失败"。
- **根因2**: pollTask 的 catch 分支显示关闭按钮但忘记绑定 onclick。
- **修复**: 后端只收集 `mysqldump: [ERROR]` 真错误;前端 `api()` 仅在 HTTP 非 2xx 时抛错;统一 closeModal 逻辑。

### R7 备份进度卡首表 + 完成无关闭按钮(16:20)
- **根因1(进度不滚动)**: 进度仅靠 stderr 解析的"表切换"事件更新,大表 dump 期间无任何更新;且 stderr 管道缓冲导致表事件延迟 → 卡在第一张表,完成时骤跳 100%。
- **根因2(无关闭按钮)**: 主流程 `out_thread.join()` 后手动 `proc.stderr.close()` 再 join,存在管道死锁风险,`run_backup` 卡死 → worker 不返回 → 任务永远 running → 弹窗无关闭按钮,只能刷新。
- **修复**:
  - 进度改为**按 stdout 实际导出字节数平滑更新**(预查表总大小作为预估总量,`已导出 X / 约Y (P%)`),不再依赖表切换;表名仅作补充显示(current)。
  - 线程收尾改为标准无死锁模式:**先 `proc.wait()`(读线程持续排空管道),再 join 两个读线程**;不手动 close stderr。
  - 表名正则修正为 `for table\s+'?([\w$]+)'?`(原正则误匹配 "table structure" 中的 structure)。
- **验证**: 500 万行大表(358MB)备份 → 21 个平滑递进进度值(0→2.9→7.9→...→100),10s 完成,任务正常 done,表名正确显示;小库备份/还原闭环通过。

### R8 原生对话框被遮挡 + 进度弹窗交互优化(17:10)
- **根因1(对话框在页面下层)**: 服务进程不是前台进程,Win32 对话框无 owner 窗口时被 Windows 弹到当前前台窗口(浏览器)之后,需点任务栏才能找到。
- **修复**:
  - 新增 `_make_topmost_owner`: 创建一个屏幕外的 WS_EX_TOPMOST 隐藏窗口作为对话框 owner(`hwndOwner`),`SetForegroundWindow` + `AllowSetForegroundWindow(ASFW_ANY)` 提权,并临时清零前台锁定超时(`SPI_SETFOREGROUNDLOCKTIMEOUT`),结束后恢复并销毁窗口。
  - `GetOpenFileNameW` / `SHBrowseForFolderW` 均挂上该 owner。
- **根因2(详情区点击无内容/体验差)**: `<details class="progress-detail">` 依赖后端 detail 日志,内容少且折叠交互不直观。→ 移除详情区(HTML/CSS/JS 同步清理),进度信息集中在 progress-msg 单行显示。
- **完成交互**: 任务结束后标题显示「✅ 操作完成 / ❌ 操作失败」,成功时附结果摘要(大小/耗时/文件路径),按钮文案区分「完成」/「关闭」,点击即关闭弹窗并刷新历史。
- **经验**: Windows 前台窗口切换有系统级限制(SetForegroundWindow 仅前台进程可调用),后台服务进程弹 GUI 对话框必须:置顶 owner 窗口 + AllowSetForegroundWindow 提权 + 清零 foreground lock timeout,三件套缺一不可。

### R9 还原目标数据库下拉为空(18:46)
- **根因**: V2 重构删除了旧定时备份表单的 `#sc-db-pick` 元素,但 `loadBackupDbs()` 仍向其赋值 innerHTML → TypeError 中断在为 `#rs-target-db` 赋值之前 → 还原目标库下拉永远空白。
- **修复**: 清理 `loadBackupDbs()` 与 `sc-scope` onchange 中所有已删除元素的残留引用。
- **验证**: jsdom 模拟 API 返回 3 库,断言 rs-target-db 选项 = (使用文件自带建库)+3 库;前端回归 10/10 通过。
- **经验**: **删除 DOM 元素后必须全局 grep 其 ID**。本次 try/catch 吞掉异常更隐蔽——catch 分支同样引用了该元素,连失败提示 toast 也一起失效。

### R10 取消对话框报 pop from empty list + 备份路径默认值(19:30)
- **根因1(取消后报错)**: R8 引入的 `_restore_lock_timeout` 用 `[0]=value` 单槽覆盖 → 首次调用后 list 变空,第二次赋值下标越界被 try 吞掉,第二次销毁时 `pop()` 抛 `IndexError: pop from empty list`,被 `_native_dialog` 捕获显示"对话框调用失败"。
- **修复**: 彻底弃用 push/pop 配对,改为**全局单槽** `_saved_lock_timeout`(global 声明 + 每次 make 时清空、destroy 时读取后置 None),三种情形(赋值失败/索引越界/重复调用)均不会再崩;并实测沙箱环境 `SystemParametersInfoW` 返回 FALSE 的路径。
- **根因2(取消时前端提示)**: 前端 `else if (r.error)` 分支无防呆;取消对话框返回 `{canceled:true}` 时后端不会填 error,但原逻辑对无 path 无 error 无操作——实际报错均来自根因1。→ 明确取消为静默(不加提示),仅 `r.path` 有值才回填。
- **功能优化(备份路径默认为空)**: 前端 `loadBackupDbs()` 不再回填 `settings.backup_dir` 到输入框;留空时由后端 `run_backup` 用全局默认目录兜底。
- **经验**: ① ctypes 全局系统状态(如 SystemParametersInfoW)返回值在受限环境可能为失败,代码不能依赖其成败;② 涉及"恢复现场"时优先用**单槽 + global**,比 push/pop 配对更不易空栈。

## 四、关键技术决策与经验(可复用)

1. **Windows 多进程端口共绑**: 改代码重启服务,必须先确认旧进程已死(netstat 查 8090 + taskkill),否则请求被分发到旧代码进程。
2. **Git Bash curl 路径转换陷阱**: Git Bash 的 curl 会把 `C:\\` 转成 `C://`(MSYS 转换),测试 API 请用 Python urllib 直连。
3. **ctypes 指针截断**: 调用返回指针的 Win32 API 必须 `restype = c_void_p`,否则 64 位指针截断 → 访问违规。这是 ctypes 最常见的崩溃坑。
4. **Windows 原生对话框**: 首选 ctypes + Win32 API(GetOpenFileName/SHBrowseForFolder),避免 PowerShell 子进程的引号转义、权限、安全策略问题;必须在 STA 线程调用(CoInitialize)。
5. **mysqldump 表级进度**: 默认 stderr 只有 warning;加 `--verbose` 输出 `-- Retrieving table structure for table X`,可解析做表级进度;按 information_schema 预查的表大小加权计算百分比。
6. **前端回归**: 用 jsdom 在 Node 中执行 app.js(stub fetch/echarts),可发现顶层绑定错误,是"点击无反应"类 bug 的快速检测手段。

## 五、测试方法

```bash
# 前端运行时回归(需 Node + jsdom 装在 managed workspace)
NODE_PATH=<jsdom所在node_modules> \
  node tests/test_frontend.js

# 备份→还原端到端(自动建测试库 test_verify,完成后清理,不碰生产数据)
python tests/test_e2e.py

# 异步任务进度验证(测试库 test_pg,8 万行)
python tests/test_progress.py

# 大表进度平滑性验证(500 万行,验证进度滚动 + 任务完成)
python tests/test_progress_big.py
```

## 六、当前已知限制与后续优化建议

1. **物理备份支持**: 目前仅 mysqldump 逻辑备份;如需物理备份(文件级),可用 Percona XtraBackup 或 MySQL Enterprise Backup,需额外开发。
2. **备份文件管理**: 尚无"下载备份到浏览器本地"功能,可通过原生文件对话框或文件服务补充。
3. **还原冲突检测**: 还原到非空库时仅二次确认,可增加"目标库表冲突清单"提示。
4. **多连接并发**: 目前同时只允许一个备份/还原任务(互斥锁),多库并行备份可后续优化。
5. **安全加固**: 服务仅绑定 127.0.0.1,若需远程访问建议加鉴权;`.secret.key` 需妥善保管(泄露可解密配置密码)。
6. **监控告警**: 可增加阈值告警(连接数、慢查询)推送。
7. **定时备份增强**: 当前 cron 精确到分钟,可支持秒级与多任务。

## 七、V3 跨平台部署改造(2026-08-24)

**目标**: 任意 Windows/Linux 主机开箱即用;数据库本机或远程皆可;首次运行 Web 向导引导配置。

### 二期:一键安装 + 服务化(2026-08-24)
1. `install.bat`(纯 ASCII+CRLF): 探测 Python → 建 .venv → pip 装依赖,幂等可重跑,结尾打印 schtasks 自启命令;
2. `install.sh`: 默认装依赖;`--service` 注册 systemd(Linux+root 校验);`--remove-service` 注销;`--print-service` 仅渲染 unit 不落盘(安全审查用);`MC_PORT`/`MC_PYTHON` 环境变量可覆盖;
3. `scripts/mysql-console.service` 模板(`__BASE_DIR__`/`__USER__` 占位符,Restart=on-failure,WantedBy=multi-user.target);
4. `INSTALL.md`: 两平台各 ≤5 步 + 开机自启方案 + 远程库权限要求 + FAQ(5 条);
5. README 快速启动改为 install → start 两段式并指向 INSTALL.md。

**二期验证记录**
- install.bat 真实全新安装实测: 删除 .venv 后运行,自动探测到系统 Python 3.14.2(py 启动器),venv 创建+依赖安装成功;重跑幂等("already exists/already satisfied");
- install.bat → start.bat → /api/health 200 全链路通过;
- install.sh 在 MSYS(Git Bash)环境实测暴露并修复一个真实可移植性问题: **Windows 商店 python 占位符**(WindowsApps/python3)会骗过 `command -v` 但无法执行 → 候选循环中先跑 `$c -c 'pass'` 验证可用再选中;新增 MC_PYTHON 显式指定逃生门;
- --print-service 渲染正确(User/WorkingDirectory/ExecStart 绝对路径);--service 非 Linux 正确拒绝;未知参数返回 Usage 与退出码 2;
- start.sh 同步加占位符防护。

**二期边界说明**: systemd 注册逻辑在真实 Linux 主机上未验证(当前环境为 Windows),首次 Linux 部署时建议先 `./install.sh --print-service` 审查 unit 内容再注册。

### 改动清单
1. **修复迁移阻断项**
   - `native_scheduler.py`: f-string 嵌套同类引号(Python 3.12+ 语法)导致 3.10/3.11 编译失败 → 抽出 `_oncalendar()` 函数,顺带修正 hourly 的非法 OnCalendar 表达式;
   - `requirements.txt`: 原钉死的 `pymysql==2.2.8` 在 PyPI 不存在(装不上)→ 放宽为 `pymysql>=1.1,<2`;
   - `start.bat` 硬编码旧机 python 绝对路径 → 自动探测(.venv → py -3 → python,均校验 ≥3.10);
   - `stop.bat` GBK 乱码重写;新增 `start.sh` / `stop.sh`(lsof 定位进程,先 TERM 后 KILL)。
2. **环境自适配(消除客户端路径硬编码)**
   - 新增 `env_probe.py`: `find_tool()` 按用户设置 → PATH → 常见目录(Windows 通配 Program Files/phpstudy/xampp/wamp 等,Linux 标准 bin)定位 mysqldump/mysql;
   - `backup_engine._cli_args()` 动态解析,找不到抛明确错误提示去设置页配置;`mysql_client.MYSQL_BIN` 移除;
   - `config_store.DEFAULT_SETTINGS` 新增 `mysql_bin`(空=自动探测)、`setup_done`;旧 config.json 启动自动补默认键。
3. **首次部署三步向导**(前端 setup-modal + `/api/setup/*` 四接口)
   - 触发: 无任何连接且未完成过引导;「连接管理」可"重新运行引导";
   - 步骤: ①环境检测表(逐项 ✓/✗/⚠ 与建议) → ②客户端目录验证(mysqldump --version 实测)+备份目录 → ③数据库连接(文案明示远程可用,test-db 实测);
   - `POST /api/setup/finish` 一个请求落盘: 设置(mysql_bin/backup_dir/setup_done=True)+新建并激活连接。
4. **远程库一等公民**
   - 连接表主机列加「本机/远程」徽标(host 判断);历史记录新增 host 字段与目标列;
   - 备份/还原前校验客户端 vs 服务器大版本,不一致在进度消息与历史记录中给 ⚠ 警告。
5. **服务设置弹窗**: 连接管理页可随时查看/修改 mysql_bin 与默认备份目录,带即时验证。

### 验证记录
- py_compile 全模块通过(Python 3.11.15);jsdom 前端回归 10/10;
- 本机(无 MySQL、无本地客户端)实启动:`/api/setup/env` 如实报告客户端缺失且 all_required_ok 仅按核心依赖计算;probe-client 对无效路径返回 400 明确报错;test-db 对无库主机返回可读的连接拒绝错误;
- finish 接口实测: 新连接加密落盘、active_conn_id 指向新连接、setup_done=True。

### 经验
1. **f-string 嵌套引号是 3.12 语法**,跨版本分发代码必须规避(尤其藏在仅 Linux 执行的分支里——编译期就炸,与运行平台无关)。
2. **钉死不存在的版本号比不写版本号更糟**(`pymysql==2.2.8`),发布前应在新环境实际跑一遍 pip install。
3. jsdom 测试的 fetch stub 返回 `[]`,新增顶层逻辑必须容错非对象响应,否则回归全灭(R1 教训的延续)。
4. **批处理编码铁律**: 含中文的 `.bat` 存成 UTF-8 + `chcp 65001` 反而必炸——cmd 切换代码页后按旧偏移继续读文件,命令从多字节字符中间被剁碎(`'e'`/`'tat'`/`'ersion_info'`)。跨环境分发的 bat 唯一可靠方案:**纯 ASCII 英文提示 + CRLF 换行**(stop.bat 原 GBK 在本机显示乱码但能跑,UTF-8 显示正常但必炸,勿重蹈覆辙)。
5. **timeout 是被占用名**: bat 内延时勿用 `timeout /t`(与 Git Bash/MSYS 环境 PATH 中 GNU coreutils 的 timeout 相撞报 `invalid time interval '/t'`),用 `ping -n 2 127.0.0.1 >nul` 任何环境都安全。

## 八、认证+双后端存储+系统设置+数据看板(2026-08-25)

在 V3 跨平台部署完成基础上,新增鉴权体系与双后端存储重构。这批工作在本地分多个提交推进,最新一次已连同告警/变量功能压缩为单提交归档(见第九章)。

### 双后端存储(轻量 vs 全量)
- **模式字段**:`config_store.DEFAULT_SETTINGS.run_mode`(`lite` 轻量=文件 / `full` 全量=系统库)
- **系统库**:默认 `_mysql_console`,6 张表 `mc_config/mc_connection/mc_schedule/mc_backup_history/mc_operation_log/mc_admin`
- **全量后端**:`system_db.py` 的 `StorageBackend` 类,读写系统库全部 CRUD;`config_store._is_full_mode()` 分派
- **防死锁同步关键**:全量模式下连接信息在系统库,但系统库连接又需连接配置来读取 → 写/激活连接后调用 `_sync_connections_to_file()` 同步到 `config_store.py` 文件层作 bootstrap 后备
- **切换**:`config_store.switch_to_full_mode()` 轻量→全量(创建系统库+迁移旧文件数据+设管理员),不可逆

### 登录认证体系(全量模式)
- `POST /api/login` → `{ok, token, username}`,token 存 localStorage;`Handler._auth_guard()` 检查 `Authorization: ***
- 免认证路径 `_AUTH_FREE_PATHS = {/api/login, /api/auth-status, /api/health, /api/request-reset-code, /api/reset-password}`
- Session 有效期 8 小时(`SESSION_TIMEOUT`)
- 找回密码:`request-reset-code` 生成 6 位数字验证码输出到**服务端终端**(10 分钟有效),`reset-password` 用验证码+新密码重置
- 修改密码:`change-password`(验证原密码);修改用户名:`change-username`
- 管理员存储:轻量存 `config_store.DEFAULT_SETTINGS.admin_password_hash`;全量存系统库 `mc_admin` 表
- 密码哈希:pbkdf2_hmac sha256 200000 轮,存 `salt_hex$hash_hex`
- 引导向导第 2.5 步可选择运行模式;settings 页在非全量时显示「切换到全量模式」区块

### 前端
- 新增 `login.html`(双栏登录页 + 找回密码两步流程)
- `init()` 流程:查 auth-status → 需登录且无 token 跳 login.html;有连接恢复主界面;无连接自动弹四步向导
- 新增页面:数据看板 `dashboard`、告警 `alerts`、服务器变量 `variables`、系统设置 `settings`

### 数据看板页面(PhaseB)
- API:`/api/dashboard/health`(健康评分)、`/api/dashboard/innodb`(InnoDB 指标)、`/api/dashboard/tablespace`(表空间 TOP)、`/api/dashboard/replication`(复制状态)

### 告警 / 变量页面(前端基本完成,后端已实现)
- `mysql_client.alerts()`:`SHOW GLOBAL STATUS` 计算连接数>100(警告)、慢查询>10/小时(警告)、活跃线程>20(严重)
- `POST /api/alerts`(只读检查)、`POST /api/variables`(`SHOW VARIABLES` 全量,前端表格可过滤)
- **注意(进行中)**:告警阈值目前**硬编码在后端**,前端「保存阈值」按钮为占位(“保存功能开发中...”),阈值尚未做成可配置 settings 键——这是本功能未完成收尾点。

### 验证记录
- py_compile 全模块通过;本地实启动 /api/health 正常。

## 九、GitHub 归档上传(2026-08-25)

- 在本机(Windows)通过浏览器设备码授权(无 gh CLI,走 HTTPS + OAuth device flow)认证为 `zetsubouk`
- 仓库:`https://github.com/zetsubouk/mysql-console`(**私有**)
- **安全前置处理**:`.secret.key`(Fernet 密钥)、`data/config.json`(加密密码)、`schedule_tasks.json`、14 个 `.pyc` 曾被 git 误跟踪(先提交后加 .gitignore 不生效)→ 全部 `git rm --cached` 剔除;因密钥已进入历史,按用户选择**压缩为单个归档提交**(`575ff8c7`,分支 `master`→`main`)
- 验证:远程根目录无 `data/`、`.secret.key`(均 404);远程仅 1 个提交
- **教训**:`.gitignore` 只能防未跟踪文件,**先提交过的敏感文件必须显式 `git rm --cached`**;密钥类文件一旦进历史,需改写历史(压缩/过滤)才能真正清除
- DEVLOG/HANDOFF 完整记录开发史,压缩历史不丢信息

## 十、环境信息(归档参考)

- 开发环境:MySQL 8.x Community,Windows 服务方式运行于本机非标端口(具体路径/端口/数据目录已脱敏)
- 生产环境:ERP/OA 业务系统库(库名与内网地址已脱敏),**操作需谨慎**,测试一律使用 test_verify 测试库

## 十一、v3.0.0 正式 Release(2026-08-26)

- 版本号定为 **v3.0.0**(首个正式发布:V3 跨平台架构 + 认证/双后端/看板/告警全部落地)
- **发布前脱敏**(commit `49b59d4`):DEVLOG 生产库名清单/内网 IP/本机 MySQL 路径端口、README+DEVLOG 的本机 jsdom 绝对路径、app.js 默认端口指纹;复查仅剩 GitHub 仓库名自身
- 打包:`git archive` 基于 tag v3.0.0 生成 tar.gz+zip(prefix=mysql-console-3.0.0/),天然排除 data/.venv;校验包内无敏感目录、start.bat 保持 CRLF+纯 ASCII
- 发布:无 gh CLI,走 REST API(`POST /releases` + `upload_url` 上资产),token 复用 git credential manager 凭据
- 验证:release 页 https://github.com/zetsubouk/mysql-console/releases/tag/v3.0.0 ,双资产 state=uploaded,远端 tag 指向 `49b59d4`
- 经验:`git archive` 从 tag 打包是零脱敏风险路径(只含已跟踪文件);发布类改动先扫描 IP/主机名/内部系统名三类指纹

## 十二、备份文件浏览与下载(2026-08-27)

- **诉求**:单机工具备份产物只能从文件系统取;局域网/远程访问场景下希望浏览器直接浏览并下载归档。属三期候选「备份文件浏览器下载接口」落地。
- **后端**:
  - `backup_engine.list_backup_files()`:列出**允许目录**(配置的 `backup_dir` + 项目默认 `data/backups`,去重)内所有 `.sql/.sql.gz`,按 mtime 倒序,返回 name/path/size/mtime/compressed。
  - `backup_engine.resolve_backup_file(raw)`:校验可下载文件——`realpath` 归一化(防 `..` 穿越)、强制后缀、必须位于允许目录内、必须存在;非法返回 `None`。
  - 新增 GET `/api/backup-files`(列表)与 `/api/backup-files/download?file=`(流式下载,`Content-Disposition: attachment` 分块写,65536 字节/块防大文件占内存)。下载路由从查询串取 file(反斜杠路径原样保留,不做 `path.split("/")` 分割)。
  - **安全**:两者都走认证守卫(`_AUTH_FREE_PATHS` 外);下载路径白名单校验**防止任意文件读取**(如 config.json/.secret.key)。
- **前端**:备份页新增「备份文件」面板(文件名/大小/修改时间/下载);历史表「备份成功」记录行加「下载」按钮;`downloadBackup()` 用 `fetch` 拉 blob 以便携带 `Authorization` token,再 `createObjectURL` 触发浏览器下载。
- **验证**:py_compile 全模块通过;jsdom 前端回归 14 项 OK + db_picker 11 项 OK;实启动 → `/api/health` 200,`/api/backup-files` 无 token 401(认证生效)、逻辑单测确认列表/合法解析正常且 config.json/.secret.key/passwd/server.py/不存在文件**全部被白名单拒绝**;`_serve_download` 流式输出 mock 断言 200/attachment 头/55 字节完整。
- **经验**:后端新增 GET 路由时若用 patch 工具插入带缩进代码块,CRLF 文件会双重换行/错位缩进——本项目 server.py/app.js/index.html 均为 CRLF,新增代码块优先用**Python 脚本 + `str.replace`** 做精确替换(绕开 patch 工具的 CRLF 归一),再 `py_compile` 兜底。
## 十三、认证凭据唯一权威 = 系统库(2026-08-27)

- **Bug 现象**:全量模式下「每次编译/重启后登录密码都需要重置」。实测确认:密码 hash 存在**两处且已不一致**——系统库 `mc_admin` 表(改密码时写入,当前值)与 `data/config.json`(切换全量时写入后从未更新的**旧影子**)。
- **根因**:`verify_admin()/is_password_set()` 原先用 `_is_full_mode()`(要求系统库**实时可达**)判定;系统库瞬时不可达(重启瞬间/连接抖动)时翻 False → 回退文件层**旧密码** → 新密码登录失败 → 只能走找回密码重置。
- **修复**:
  - 新增 `config_store._is_full_config()`:只看文件层 `run_mode == "full"` 配置标记,**不依赖系统库实时可达**。
  - 全量模式下管理员 API(`verify_admin/is_password_set/get_admin_username/set_admin/set_admin_password`)只读写系统库,**绝不回退文件层**;系统库不可达时 `verify_admin` 抛自定义 `SystemDbUnavailable`,`is_password_set` 保守返回 True(保持登录页,防误判「未设密码」绕过认证)。
  - `server._handle_login` 捕获 `SystemDbUnavailable` → 503「系统库不可用,无法验证登录」明确提示(不再静默回退)。
  - `switch_to_full_mode` 不再把凭据写进文件层,文件层 `admin_username/admin_password_hash` 清零(影子清除)。
- **数据落库**:当前实例文件层影子已备份(`data/config.json.bak-20260827`)后清空;密码/用户名唯一权威 = 系统库 `_mysql_console`.`mc_admin`。轻量模式无登录页,凭据仍走文件层,不受影响。
- **验证**:py_compile 全通;实启动后 auth-status 文件层已空但 `password_set=True`(走系统库);错密码 401 而非 503/回退;monkey-patch 模拟系统库不可达:`verify_admin` 抛 `SystemDbUnavailable`、`is_password_set` 保守 True;jsdom 前端 14 项 + db_picker 11 项全 OK。
- **经验**:双后端适配器里「功能数据回退文件层」是 bootstrap 设计,但**认证凭据必须单点权威**——任何回退路径都会造成旧密码残留/安全漏洞(宕机期间旧密码仍然可用)。
## 十四、引导保存三连环:Bootstrap 死锁 + config.json 写坏(2026-08-27)

- **现象**:全量模式下改 MySQL root 密码后,重新运行引导输入新连接 → 测试连接成功,保存时报「数据库密码错误」;保存后首页右上角选库提示密码错误、连接未激活。
- **环1(bootstrap 死锁)**:`/api/setup/finish` full 分支顺序是 `set_admin` → `save_connection`。`set_admin` 内部 `_get_backend()` 用**文件层激活连接(旧密码)**连系统库 → Access denied → 报「数据库密码错误」并中断 → 连接保存/激活根本没执行。修复:finish 顺序改为**先保存并激活连接(bootstrap 立即刷新为新密码)→ 再 init/import/set_admin**。
- **环2(反向同步缺失)**:用户改数据库密码后,系统库通道(bootstrap)握着旧密码——新密码只存在于用户刚保存的连接里 → 死锁:「系统库需新密码才可写、新密码只在刚保存的连接里」。修复:新增 `config_store._wake_system_db_after_save(payload, cid)`——保存连接后用其新凭据直连系统库 upsert 该连接并刷新 bootstrap。
- **环3(config.json 写坏,隐藏主凶)**:`_sync_connections_to_file()` 里 `item = dict(c)` 把系统库行**原样**带入文件层——带 `username`/`is_active`/`created_at(datetime)` 系统库列名。`json.dump` 遇 `datetime` 抛 TypeError → **文件写半截损坏**(实测 216 字节截断)→ 后续一切读配置操作崩,表现为「密码错误/连接未激活」。修复:

  - `_sync_connections_to_file` 显式构造文件层格式(id/name/host/port/user=username/note/password重加密/active=is_active),剥离 datetime;

  - `_save` 加 `default=str` 兜底(防 datetime 再炸);

  - `_load` 对损坏 JSON 容错返回最小结构(防启动即崩,系统库可重建)。
- **现场恢复(外部注入)**:config.json 已被覆盖成最小结构→bootstrap 断→登录验证又需系统库…死锁。解法:从 `data/config.json.bak-20260827` 恢复完整结构(full+连接),用环境变量注入新密码刷新 bootstrap(密码不进代码/历史),系统库通道恢复。**教训:修复代码后别在本机旧代码进程上验证**——用户手动启动的服务是旧代码,它仍在跑会继续写坏文件;必须确认 8090 被哪个 PID 持有(用户服务=Hermes runtime python,非 .venv)先全杀再换新。
- **验证**:修复后 E2E——health 200 / auth-status password_set=True / bootstrap 可连系统库 / 目标库连通(系统库 6 表)/ config.json 完整无 datetime 残留。
- **工具经验**:bash 环境下 `taskkill //F`、`cmd //c`、netstat/wmic 在 Git Bash 都不可靠(wmic terminate 报 RC=0 进程却还活着);**权威进程操作走 PowerShell 脚本文件**(`Get-NetTCPConnection`/`Stop-Process -Force`),`$` 变量会被 bash 吞,所以必须写 .ps1 文件再 `powershell -File` 执行。


## 十五、v3.1.0 发布:存储统一 + 引导/布局/备份还原修复 + 面板增强(2026-08-27)

- **存储层统一重构**(核心):轻量模式全部改存本地 SQLite(`data/config.db`,新增 `local_store.py`,stdlib sqlite3 零依赖);全量模式连接/配置/日志/历史/管理员**唯一来源 = MySQL 系统库**,本地只留「最小 bootstrap」(run_mode/sys_db_name + 一条能连系统库的连接)。删除旧的「文件层整份镜像连接列表」(`_sync_connections_to_file`)——根治全量模式「删了又回来/残留」的双存储混乱。`config_store._load/_save(JSON)` 全部改走 SQLite;旧 `config.json`/`schedule_tasks.json`/`backup_history.json` 启动自动迁移。
- **重新引导 = 彻底重装**:`/api/setup/finish` 检测已配置过 → 用旧 bootstrap 尽力 DROP 旧系统库 + `reset_local()` 清空本地 → 全新初始化。用户确认破坏性、仅本次执行。
- **Bug 修复**:
  - 全量模式备份/还原报「错误: 'user',无法备份」:系统库连接行字段是 `username` 不是 `user`,而 `backup_engine` 用 `conn_cfg['user']` 直取 → KeyError。修复:`StorageBackend.list_connections/get_connection` 归一化 `user=username`,并把 `backup_engine` 取用改 `.get('user','root')` 容错。
  - 还原进度超 100% / 用压缩包大小误算:`.gz` 还原读的是解压字节,分母却是压缩包大小(1G 数据 149MB 包就到 100%)。修复:读 gzip 尾部 ISIZE 作解压大小作进度分母,按实际数据量核算。
  - 初始化全量自定义系统库名 split-brain(建两个库/默认仍用旧名):全量初始化先落 `prepare_full` 到本地 meta/bootstrap,再建库,链路唯一权威。
  - 初始化引导「运行模式」步骤不可达(死代码):`suGoto` 只遍历 1..3 不渲染 `su-pane-mode`。修复:步骤重排为 1 环境/2 客户端目录/3 运行模式/4 数据库连接,`SU_PANE_FOR` 映射。
  - 登录无用户名输入(全量模式):login 页加用户名框 + `/api/login` 校验用户名匹配。
  - datetime 序列化报错(连接列表/日志 500):`_send_json` 加 `default=str`。
  - 首次安装进入时后台显示概览监控并强制弹引导:未初始化改显示空白欢迎页(新增 `setupLanding` + `#welcome-banner`)。
  - 引导全量模式/账户设置/服务设置弹窗/第4步数据库连接字段排版乱:统一 `.f-field` 容器(标签在上/输入在下,按连接名称→主机→端口→用户名→密码排序)。
- **增强**:
  - 数据看板健康评分:52px 数字顶出圆 → 改环形进度仪表(`conic-gradient` 圆环 + 居中数字)。
  - 服务器变量新增「含义/说明」列:新增 `variable_docs.py`(83 个常用 MySQL 变量中文说明,未收录给官方文档链接),`/api/variables` 返回 `desc`。
  - 备份/还原目录默认带入初始化设置的备份路径;还原选择器默认从备份目录起步。
  - 操作日志入库(全量模式写 `mc_operation_log`),`/api/logs` 改读库;登录/改密/连接管理等操作埋点(带操作人)。
- **验证**:py_compile 全通;轻量 SQLite 隔离冒烟(连接/设置/调度/历史 CRUD + reset 真清空);jsdom 前端回归全过;服务重启后 `/api/health ok`、未初始化走空白欢迎+引导;`_gz_uncompressed_size` 实测 9759B 包→4000000B 解压 ISIZE 精确匹配。
- **要点**:SQLite `with connection` 只 commit 不 close 会占句柄导致 reset 删不掉,须 contextmanager `finally close` + WAL 三文件齐删;系统库行为 `username`,`统一补 user`。
- **发版**:v3.1.0,git archive tar.gz/zip 双资产,REST API 发布(见本文件方法同第十一章)。

## 十六、一键初始化 + 服务器变量导航 + 轻量切全量修复(2026-08-27)

### 16.1 一键初始化脚本(init.bat / init.sh / cli_init.py)
- **定位**:与 start.bat / stop.bat 平级,纯脚本 + 终端确认,把系统一键重置到"首次配置"全新状态。
- **流程**:停掉 8090 旧进程 → 探测 Python → `python cli_init.py --check`(只读检测并打印信息汇总:
  配置状态 / 运行模式 lite·full / 系统库名+可达性 / bootstrap 连接信息 / 本地 config.db / .secret.key /
  8090 端口 / 备份目录+备份文件数 / 待删文件清单) → 终端 `Type 'y' to confirm` → `--do --force` 执行清理。
- **清理逻辑**(按模式):
  - 轻量模式:删 `data/config.db(+wal/shm)`、`config.json*`、`.secret.key`、`data/logs/*`。
  - 全量模式:先用 bootstrap 连接 DROP 系统配置库(尽力,失败仅警告不中断),再同上清理。
  - 两种模式都遍历清空备份目录(配置的 backup_dir + 默认 data/backups)内全部文件,保留目录本身。
  - **边界**:只删系统配置库/本地配置,绝不碰被管理的生产库(如 ERP/OA 系统库等);保留程序源码/依赖/目录使系统保持可用。
- **双层确认**:init.bat 做终端 y/N;`--do` 不带 `--force` 时自身也会二次确认(防手敲误删)。
- 复用之量大机制:`mysql_client.drop_db`(与 setup/finish 重新引导同一函数)+ `config_store.reset_local`。

### 16.2 服务器变量导航 + 含义说明(需求调整)
- **导航**:服务器变量入口从分隔线下管理区移到左侧主功能区,**紧跟数据看板下方**(概览→数据看板→服务器变量→数据库→…);
  index.html `.nav` 与 app.js `PAGES` 顺序同步。
- **含义说明**:`variable_docs.py` 未收录/无法给出准确含义的变量 fallback **由"参考官方文档链接"改为留空**;
  已收录 83 条准确说明保留。前端 `v.desc || ""` 天然处理空值。

### 16.3 Bug: 轻量模式切换全量失败(无可用连接配置)
- **现象**:轻量模式下系统设置填用户名/密码点「确认切换」报「无可用连接配置,无法切换全量模式」。
- **根因**:`switch_to_full_mode` 用 `_get_bootstrap_conn_cfg()` 取连系统库凭据,但 **bootstrap 只在全量模式存在**(prepare_lite 从不写),
  轻量模式连接都在本地 SQLite `connections` 表。
- **修复**:新增 `_resolve_full_mode_conn_cfg()`——bootstrap 优先,轻量模式回退到本地**活动连接(无则第一个)**取明文凭据;
  切全成功后 `_set_bootstrap(conn_cfg)` 固化 bootstrap(防重启后连不上,延续"反向唤醒缺失"坑)。

### 16.4 Bug: 轻量切全量后刷新页面模式回退轻量
- **现象**:切全量后系统库已建(列表可见),但刷新页面模式又变回轻量。
- **根因**:前端经 `/api/settings` 的 `run_mode` 判模式;全量分支 `get_settings()` 读**系统库 mc_config**,
  而切换时只改了本地 meta=full,系统库存值仍是迁移来的 `lite` → 刷新读到 lite。
- **修复 1(根治)**:`get_settings()` 全量分支强制 `run_mode="full"`、`sys_db_name=_sys_db_name()` 对齐本地 meta 真值,
  不依赖系统库存值(本地 meta 是唯一权威)。
- **修复 2(需求:清轻量数据)**:新增 `local_store.clear_lite_data()` 清空本地 SQLite 的 `connections`/`settings` 表 +
  调度/历史 JSON meta,**仅保留最小 bootstrap meta**(run_mode/sys_db_name/bootstrap/setup_done,删不得,否则血泪陷阱 8 死锁);
  `switch_to_full_mode` 末尾调用。同时把 run_mode/sys_db_name 写回系统库保持一致。

### 16.5 验证
- `cli_init.py` 轻量/全量端到端(隔离副本):--do 后 data 目录 0 文件、程序仍可 import=保持可用;全量 DROP 分支对不可达 bootstrap
  容错不崩、不连生产、不碰真实库;init.bat 纯 ASCII+CRLF(62 行)校验通过。
- `get_settings()` 单测(mock 系统库存 lite):强制返回 run_mode=full + 本地 meta 的 sys_db_name ✅。
- `clear_lite_data()` 单测:保留 bootstrap/run_mode/sys_db_name,清空连接 0 行 / 设置 0 行 / 调度历史 ✅。
- 全模块 py_compile 全通;jsdom 前端回归 14/14(导航绑定 11/11)。
- 提交:cd24a6d(初始化)、804b303、71ecd7d(导航/说明)、bfed761(模式回退修复)。
## 十七、数据库管理(用户管理 + 数据库重启)

> 2026-08-27。需求:①用户管理融入「用户与连接」页顶部(顺序:用户管理 / MySQL 用户 / 当前连接);②服务管理砍掉启停,仅保留「重启」+状态检测,直接放「数据库」页,不建独立服务管理页。

### 17.1 新增 service_manager.py(跨平台服务管理)
- `detect_service_name()`:Windows 枚举 `sc query` / macOS `brew services list` / Linux `systemctl list-unit-files` 找 mysql/maria,再对已知名(MySQL80/MySQL/...mysqld)存在性兜底。
- `_service_state()`:返回 running/stopped/unknown/missing(Windows 解析 `sc query` STATE,Linux 用 `systemctl is-active`,macOS `brew services info`)。
- `restart_service(name, verify_cb=None)`:Windows `net stop/start`(需管理员)、Linux `systemctl restart`、macOS `brew services restart`;随后轮询 `_service_state` + `verify_cb(数据库连接测试)` 直到就绪(默认 90s 超时),返回 {ok,msg,running,elapsed}。
- `_run()` 用 bytes 解码 + `errors="replace"`(Windows 命令输出常为 GBK,`text=True` 会炸 reader 线程)。
- 服务名可手动覆盖:新增 settings 键 `mysql_service_name`(加入 DEFAULT_SETTINGS,旧配置自动补齐)。

### 17.2 mysql_client 用户管理(建/删/改密/授权)
- 新增 6 函数:`create_user` / `drop_user` / `change_user_password` / `grant_privileges` / `revoke_all_db` / `show_grants`。
- **权限白名单 `_ALLOWED_PRIVS` + 预设 `_PRESET`**:readonly(只读)、dataentry(增删改查)、struct(结构管理)、all(完整权限);前端提供自定义逐项勾选。
- **注入防护**:用户名/主机/库名一律白名单正则校验后才拼接(GRANT ... ON db.* 的库名无法参数化);密码走 `%s` 参数绑定。
- **PyMySQL % 格式化陷阱**:建库连接无默认库,`GRANT ... ON *.*` 的全局范围必须写 `*.*`(写 `*` 会报 1046 No database selected);内联通配主机 `%` 必须 `_qh()` 转成 `%%`(PyMySQL 用 Python % 运算),且无参 execute 也要传 `()` 强制 mogrify 才能 `%%`→`%`。
- **错误归一**:统一 `_exec(conn,sql,args,op)` 把 pymysql.MySQLError 转 DbError(如重复建用户 1396 → “创建用户失败:...”),避免原始异常冒泡成“服务器错误”。

### 17.3 server.py API
- `GET /api/service/status`:服务状态 + 活动连接可达性(db_reachable);`POST /api/service/restart`:重启+自动验证(带 90s 轮询)。
- `GET /api/users/<user>@<host>/grants`、`POST /api/users`(建用户+授权)、`PUT /api/users/<u>@<h>`(改密/编辑授权=先 revoke_all 再 grant)、`DELETE /api/users/<u>@<h>`。
- `_parse_user_path`:从 `/api/users/<u>@<h>[/grants]` 解析,并对 `user@host` 段 `urllib.parse.unquote`(前端 encodeURIComponent)。
- 全部路由经 `_auth_guard`(全量模式需登录);隔离已自测:6 个新路由未认证均 401。

### 17.4 前端
- 「用户与连接」页顶部新增「用户管理」面板:新增用户 / 查看授权 / 设置权限 / 改密 / 删除(删除走 confirmDialog 二次确认)。
- 「用户管理」下方保留「MySQL 用户」只读总览 + 「当前连接」进程列表,顺序符合需求。
- 「数据库」页「数据库列表」panel-head 加状态徽标 `#db-svc-status` + 「重启数据库」按钮;切换页/刷新时调 `loadDbServiceStatus()`,重启后回读状态。
- 新增用户编辑弹窗:主机(%/localhost)、授权范围(全部/指定库多选)、权限模板按钮 + 自定义勾选。
- 依赖:`.priv-grid` CSS、`window.umViewGrants/umEdit/umPwd/umDel` 供内联 onclick。

### 17.5 验证
- mysql_client 6 函数对 127.0.0.1:3306 实测(测试 user test_mc_user@% + test_verify 库):建用户→授权→重复建友好报错→SHOW GRANTS→新用户可连可读→改密旧密码失效/新密码可连→全局授权 *.*→GRANT OPTION→注入被拒→删除无残留,全部通过。
- service_manager 本机实测:自动探测 `MySQL80` + 状态 running。
- 后端全模块 py_compile 通过;jsdom 回归扩至 27 项(新增用户管理按钮/三个弹窗/表单元素/双击「用户与连接」「数据库」页切换),全部 [OK]。
- 实启动健康 + 6 个新路由未认证 401(隔离自测,未触碰生产/系统库)。
- 测试数据 test_verify 已清理;未对生产库做任何写操作。
## 十八、系统自动更新 + 仓库转公开(方案A)

> 2026-08-27。需求:检测 GitHub releases → 提示更新 → 下载/备份/应用/重启。附本次将私有库按方案A转为公开库。

### 18.1 仓库转公开(方案A)
- 前置脱敏:清理当前 HEAD 残留的生产库名、内网IP、本机路径/端口等指纹;环境探测的多盘路径样本属通用功能保留。
- 历史含敏感数据,不能直接 `PATCH private=false`。方案A=新建干净公开库:重命名旧私有库→`mysql-console-archive`(保留完整历史+旧 release),新建公开 `zetsubouk/mysql-console`,推送单干净压缩提交(43 文件,38b1bef),重发 v3.2.0 release(双资产)。
- 本地 `origin` 改指公开库、`main` reset 到干净单提交。此后提交直接 push 公开库,但**严禁再提交生产库名/内网IP/本机路径端口**。

### 18.2 version.py + /api/version(版本收敛)
- 新增 `version.py`(`__version__="3.2.0"`)为单一版本源;`/api/version` 暴露;前端「系统信息」改从 API 读(不再硬编码)。发版只改 version.py。

### 18.3 updater.py(检查/下载/备份/应用脚本)
- `check()`:`GET api.github.com/repos/zetsubouk/mysql-console/releases/latest`(公开仓库,无凭证),比较版本(去 v、取数字段),网络失败返回 offline=True 不打扰。
- `prepare()`:下载资产(Windows 选 .zip)→ 校验大小 → 解压到 `data/updates/staging/<ver>/src` → 备份当前代码到 `data/updates/backup/<cur>/`。
- `build_apply_script()`:生成独立离线脚本(等 8090 释放 → 删旧代码项(保留 .venv/data/dist 等)→ 用 staging 替换 BASE_DIR → 写 update.log → 按 start.bat/start.sh 重启)。
- 自更新核心约束:运行中的 Python 无法替换自身 .py(Windows 文件锁),故走独立脚本 + 主进程 `os._exit(0)` 释放锁。更新只替换代码,绝不碰 data/。

### 18.4 server.py API + 后台检查
- `GET /api/version`、`GET /api/update/check`(即时)、`GET /api/update/badge`(读缓存,避免每次即时打 GitHub)、`GET /api/update/status`(读 update.log)。
- `POST /api/update/prepare`(下载+校验+备份)、`POST /api/update/apply`(无可用更新时拒绝;有则生成脚本→后台启动→3s 后 os._exit(0))。
- `_update_loop` 后台线程:按 settings `update_check_interval`(off/hourly/daily/weekly,默认 weekly)定时调用 check 并缓存到 `_update_cache`(前端徽标读取);DEFAULT_SETTINGS 新增 `update_check_interval`/`update_last_check`。

### 18.5 前端
- 顶栏「⬆ 有新版本」徽标(有更新才显示,点击去系统设置);「系统信息」版本动态化 + 更新状态行。
- 「系统设置」新增「软件更新」面板:当前/最新版本、检查频率(保存)、「检查更新」「下载并准备更新」「应用更新(重启)」按钮 + 更新日志展示。

### 18.6 验证
- updater.check() 直连 GitHub 实测:current=3.2.0 latest=3.2.0 has_update=False,资产 2 个可枚举;prepare() 当前最新时正确短路不下载。
- 隔离冒烟:下载当前 3.2.0 zip(大小校验)+解压(server.py/static 在)+备份当前代码+生成应用脚本(py_compile 语法合法),链路通过;未触发真实自更新(self-apply 需用户在界面手动触发)。
- 后端全模块 py_compile 通过;jsdom 回归含更新 UI 全通过;实启动 health + 全部 /api/update/* 路由未认证 401(隔离自测)。
## 十九、修复 v3.3.0 数据库整体故障(_q 函数遮蔽 + 连接保存字段错配)

> 2026-08-27。运行中版本概览/数据看板/数据库一览/用户与连接/连接配置保存 全部报错。整体排查定位两处后端缺陷(均为系统库重构新代码引入)。

### 19.1 根因1:_q 函数名遮蔽(全站监控 API 崩溃)
- `mysql_client.py` 第 63 行原本就有 SQL 执行器 `def _q(conn, sql, args=None)`(监控/看板/用户查询全走它)。
- 8-27 新增「用户管理」时在 392 行又定义了一个单参清洗函数 `def _q(s)`(脱引号/反斜杠),把前者**遮蔽**。
- 后果:`server_overview`/`database_list`/`user_list`/`process_list`/`monitor_metrics`/`health_score`/`innodb_metrics`/`tablespace_top`/`replication_status`/`alerts`/`variables` 全部报 `_q() takes 1 positional argument but 2 were given`,映射为前端「服务器错误:加载失败」。
- 修复:清洗函数改名为 `_clean(s)`,7 处调用点(建/删/改密/授权/撤权/查授权/校验)同步改名。重命名而非改执行器,最小侵入。
- ⚠ 教训:模块内严禁重名函数遮蔽;新增辅助函数命名前缀要足够区分(`_qh`/`_clean`/`_q` 语义各不相同)。

### 19.2 根因2:连接保存 UPDATE 用错列名(Unknown column 'user')
- `system_db.py` `StorageBackend.save_connection` 的 **UPDATE 分支**(cid 已存在=编辑连接)按前端字段名 `user` 直接拼 SQL → `UPDATE mc_connection SET user=%s`。
- 但 `mc_connection` 表列名是 **`username`**(INSERT 分支一直写 `username` 所以新建连接正常,编辑才炸)。
- 后果:编辑/保存既有连接报 `(1054, "Unknown column 'user' in 'field list'")`。
- 修复:UPDATE 分支将 `user` 字段映射到 `username` 列(host/port/name/note 顺序不变)。
- ⚠ 教训:系统库列名以 INSERT 字面量为准(既有结构化文档可能滞后);前端字段名 ≠ DB 列名,写 UPDATE 时必须有显式映射。

### 19.3 验证
- 修复后 12 个 `_q` 衍生函数实测 12/12 通过(实时连接 127.0.0.1:3306,覆盖概览/数据库一览/用户与连接/数据看板/变量/告警)。
- 连接保存 CRUD 闭环:INSERT 临时连接→UPDATE(编辑,原报错分支)→回读校验 username/user 均已更新→删除清理,assertions 通过;未触碰任何生产配置。
- 后端全模块 py_compile 通过;实启动 `GET /api/health -> {"ok": true}`(重启后新代码生效)。
- 本次仅改 `mysql_client.py` + `system_db.py`(纯后端,无前端改动,jsdom 回归非本次必跑项)。
## 二十、发版 v3.4.0(UI 全面现代化 + 概览监控增强)

> 2026-08-27。前端零框架下完成设计体系重构与监控能力扩展,纯前端+少量后端增量,不改数据层。

### 20.1 UI 现代化(static/)
- 设计令牌全量重构 style.css:色彩/圆角/阴影/间距变量化,**深浅色双主题**(`data-theme` + localStorage 持久化),卡片 hover 层次、表格主色高亮、徽章语义色系、toast/空状态组件。
- 侧栏 11 项平铺 → **4 组分组导航**(监控中心/资源管理/数据保护/系统)+ 全 SVG 线性图标(替换 Unicode 字符与 emoji);顶栏连接选择器升级为胶囊(本机/远程徽章)+ 状态点动画 + 主题切换按钮。
- app.js:ICON 图标库(替换文件树/环境检测/复制状态/告警/进度弹窗全部 emoji)、面板标题关键词自动配图标、toast 动效化、环境检测状态徽章化(通过/未通过/缺失)。
- login.html 同步同一套 token,与主界面风格统一。

### 20.2 概览监控增强(实时监控 Tab)
- 实时监控面板 4 Tab:连接数/QPS(原)| **系统资源** | **InnoDB 深度** | **复制**。
- 新增 `sys_resources.py`(纯标准库优先):CPU/内存/磁盘空间全平台(Windows ctypes / Linux /proc),IOPS/网络吞吐 psutil 可选依赖,缺失自动降级隐藏;CPU 双采样差值 + 10s 缓存。
- `mysql_client.py` 新增 `monitor_full()`:一次 1s 双采样合并连接/QPS/InnoDB 命中率·脏页比·锁等待增量·读写 KB/s + 复制延迟/线程状态。
- 新接口 `GET /api/monitor/full`、`GET /api/sys-resource?disk=`。
- 阈值配色:Gauge 按健康区间变色(CPU/内存 60/80、磁盘 70/85、命中率 95/90、延迟 5/30);系统资源 Tab 仅本机连接显示(远程 DB 无宿主 OS 数据);非从库自动隐藏复制 Tab。
- 趋势图 X 轴固定 60 索引 + 相对时间标签(-60s/-120s/-180s/-240s),避免采样初期时间戳重复。

### 20.3 关键坑(经验)
- **ECharts canvas 不支持 CSS 变量**:图表配色必须 JS 运行时解析(getComputedStyle)成具体色值;主题切换需显式 setOption 刷新。
- **Gauge 的 title/detail/axisLine 属 series[0]**:刷新配色必须 `setOption({series:[{...}]})` 嵌套,写顶层 key 不生效(本次因此修了暗色下 Gauge 文字不可见)。
- SVG 无 width/height 默认渲染 300×150:动态注入图标必须带尺寸或 CSS 兜底(环境检测状态曾因 300×150 巨型图标看不出通过与否)。

### 20.4 验证
- 前端 `node --check` 通过;Python 全模块 py_compile 通过;JS 引用 id 与 HTML 全部核对无缺失。
- sys_resources 实测:内存/磁盘多盘符正常、CPU 二次采样出值、无 psutil 时 IOPS/网络正确隐藏。
- 服务冒烟:页面 200、新 API 未登录 401(认证守卫正常)、Tab 结构齐全;测试后已停服务释放端口。
- 已知限制:系统资源监控的是**运行本工具的主机**,被管远程 MySQL 仅能获取 MySQL 自身指标。

## 二十一、修复 v3.4.0 升级检测失效 + 发版 v3.4.1(2026-08-28)

> 本地 v3.3.0 实测「检查更新」永远提示"已是最新",即使 GitHub 已发布 v3.4.0。定位为 `updater.py` 的版本比较符号写反,致 `has_update` 恒为 False;该 bug 自自动更新功能引入(commit 0911954)起便存在,并随 v3.2.0/v3.3.0/v3.4.0 一路传播。

### 21.1 根因:`compare()` 返回符号写反
- `updater.py` `compare(current, latest)` 实现 `return (c < l) - (c > l)`,标准写法应为 `(c > l) - (c < l)`。
- 后果:`current < latest`(有新版本)时返回 **+1** 而非 -1;`check()` 用 `compare(cur, lat) < 0` 判更新 → 恒为 False → 永远「已是最新版本」,顶栏徽标/更新说明/下载按钮全部不触发。
- 反向场景(本地版本反而高于 latest)会误报"有新版",边界错误。
- 修复:`updater.py` 改一行 `return (c > l) - (c < l)`。
- ⚠ 教训:布尔表达式 `(a<b)-(a>b)` 与 `(a>b)-(a<b)` 仅差一个符号且含义完全相反,写比较函数务必配三组用例(current<latest/latest<current/相等)实测,勿只看真值表。

### 21.2 验证
- 直连 GitHub:本地 3.3.0 vs latest v3.4.0 → 修复前 `has_update=False`,修复后 `has_update=True`。
- 完整真实自更新链路跑通:v3.3.0 → `prepare`(下载/校验/解压 v3.4.0+备份)→ `apply`(生成 apply_update.py 独立进程)→ SWAP → 自动重启 → `version` 落为 3.4.0 → `update/status` 读回三行 update.log。
- 升级后自检 current=3.4.0=latest → has_update=False(正确);证明 3.4.0 自身带同一 bug、无法再自我升级,故须发 v3.4.1 首修。
- v3.4.1 复检:version=3.4.1,`compare(3.4.1,<future>)` 再现 bug 已修复。

### 21.3 版本号
- `version.py`:`3.4.0 → 3.4.1`。

## 二十二、v3.4.2:修复「完整权限」模板无反应 + 软件更新面板无条件展示新版更新日志(2026-08-28)

> 本地实测两个问题:①用户管理「权限模板-完整权限」点击无任何反应;②软件更新面板只在检测到新版时显示更新日志,已是最新时看不到最新版本更新内容。均纯前端修(后端授权链路本就支持完整权限)。

### 22.1 修复「完整权限」模板点击无反应
- 根因:权限模板 `data-preset="all"` 映射到 `UM_PRESETS["all"]=["ALL PRIVILEGES"]`;`umSetPrivs()` 据此去勾选权限网格 `#um-privs` 的具体权限 checkbox,但格子 value 是细分权限名(SELECT/INSERT/...),**没有一个叫 `ALL PRIVILEGES`** → `set.has()` 全 false → 视觉上"点击无反应"。其它模板(readonly/dataentry/struct 的元素均是 UM_PRIVS 子集)正常。
- 修复:点「完整权限」直接全选 `UM_PRIVS`(等效完整授权,含 GRANT OPTION)。
- 新增回归测试 `tests/test_um_preset_all.js`。

### 22.2 软件更新面板无条件展示最新版本更新日志
- 需求:无论有无最新版本,都在「最新版本」下方、「检查频率」上方展示最新版本更新日志(release body)。
- 实现:index.html 新增 `#up-latest-log` 区域;`loadUpdatePanel`/`checkUpdateNow` 无条件填充(offline 时显示"离线,日志暂不可用")。
- 新增回归测试 `tests/test_update_log.js`(真实交互路径:点击系统设置 → loadUpdatePanel)。

### 22.3 验证
- 三套 jsdom 回归全通过:test_frontend(27 OK,原回归无损)、test_um_preset_all(ALL PASS)、test_update_log(ALL PASS 覆盖已是最新/有更新/离线三场景 + 位置断言)。
- `node --check` 通过;后端 py_compile 通过(本轮纯前端,后端未动)。
- 线上实例(静态读工作树)已确认含 `#up-latest-log` 区域与填充逻辑。
- ⚠ 教训:Python `open(file, "w")` 重写含 CRLF 的源码文件会把行尾转成 LF,产生全文件假 diff;改行尾敏感文件须用 `open(..., "wb")` 字节级处理或 `newline=""` 并显式转回 CRLF。

### 22.4 版本号
- `version.py`:`3.4.1 → 3.4.2`。

---

## 二十三、API 回归测试打底 + 数据目录重定位 + 登录锁定状态修复(2026-08-28)

> 背景:server.py 单文件 1428 行、40+ 路由,此前 **HTTP 层零自动化覆盖**(只有 jsdom 前端回归 + 备份 e2e,每次事故都靠「实启动 + curl」手工验证)。本轮新增 API 层回归测试;顺手清理 remote URL 明文 PAT、同步文档。

### 23.1 数据目录重定位(MC_DATA_DIR)
- **local_store.py**:`DATA_DIR` 支持环境变量 `MC_DATA_DIR`(默认仍为项目 `data/`);**backup_engine.py** 同步——默认备份目录 `DEFAULT_BACKUP_DIR` 与历史/日志路径都走同一根。
- 用途:① **测试隔离**(API 测试的 config.db/.secret.key/备份目录全落临时目录,真实 data/ 零接触);② **可移植部署**(数据目录可放别处)。
- 改动面:仅 2 个文件各一行级改动;**未设环境变量时行为与原先完全一致**(回归风险≈0)。

### 23.2 真 bug 修复:登录锁定状态在全量+系统库不可达时 500
- **根因**:`get_admin_lock_status()` 全量分支直接 `_get_backend().get_admin()`,系统库不可达时抛 `SystemDbUnavailable`;调用点(server.py `_handle_login` 第 1304 行)**不在 try 内** → 冒泡成 500「服务器错误…」,违背十三章定的「系统库不可用 → 503」约定。
- **修复**:`get_admin_lock_status()` 改为吞异常返回 `(False, None)`(与 `add_operation_log` 等既有吞异常模式一致);真实校验仍由 `verify_admin` 抛 `SystemDbUnavailable` → 503。
- **验证**:test_99 在无真实 MySQL 下断言 login 503 且错误含「系统库不可用」。

### 23.3 tests/test_api.py(新增,18 项)
- **机制**:进程内 `ThreadingHTTPServer`(端口 0 随机)+ 临时 `MC_DATA_DIR`;urllib 直连;测完 shutdown + 删临时目录(`tests/_api_tmp`,已加入 .gitignore)。
- **覆盖**:health / auth-status / setup-env / version / settings 读写 / 连接增删改查+激活 / 调度增删改查+启停 / 备份历史 / 备份文件列表+下载 / **下载白名单(允许目录外的 .sql 与敏感路径一律 404,防任意文件读取)** / 备份降级链路(无活动连接 400「请先激活连接」;假连接备份任务以**可读错误**终止而非 500+Traceback)/ probe-client 与 test-db 错误可读 / 静态页 / 轻量日志空 / **全量模式认证守卫**(monkeypatch `is_password_set`=True 模拟已设密码:受保护路由 401、免认证路径 200、假 token 401、login 503)。
- **刻意不测**(需真环境/交互):`/api/dialog`、`/api/browse`(Win32 弹窗)、`/api/update/*`(真网络)、`/api/service/restart`(真重启)、真实备份还原闭环(已有 test_e2e.py)。

### 23.4 remote URL 明文 PAT 清理
- `.git/config` 的 origin 曾为 `https://<user>:ghp_xxx@github.com/...`(2026-08-25 上传时遗留)。已 `git remote set-url` 去掉凭据,推送靠本机凭据管理器;新增 HANDOFF 陷阱 #11(remote URL 严禁内嵌 token)。

### 23.5 文档同步
- README:项目结构与测试节更新(SQLite 双后端现状 + `tests/test_api.py`);「数据存储于 data/schedule_tasks.json」改为 SQLite。
- MANIFEST.txt:按 `git ls-files` 重新生成(49 文件/1559 KB,旧 v3 迁移清单已过期)。
- HANDOFF:状态表、验证方法(②½)、待办表(SQL 查询立项)、陷阱 #11、data 目录注释更新。

### 23.6 验证记录
- py_compile:server/config_store/local_store/backup_engine/tests/test_api 全通过。
- `tests/test_api.py`:**18/18 OK(约 8.4s)**;隔离开局自检(模块级断言 DATA_DIR==临时目录)通过,真实 data/ 未触碰。
- 无前端改动,jsdom 回归本轮非必跑项。

### 23.7 经验
1. **本机沙箱环境无可用 pip**(`.venv` ensurepip 失败、pip 安装机制连工作区内的临时目录写入都被拦):依赖改用「urllib 下载 wheel + zipfile 解包到 `_pydeps`(gitignore)」纯标准库方案——**仅限本开发环境**,产品安装仍走 install.bat/.venv。
2. **接口回归断言先看真实响应结构再写**:test_03 曾按猜测断言 `"python" in j`,实际键是 `items` 列表;跑一次修正后全绿。
3. **PowerShell 向 `python -c` 传含引号代码易被外层引号吞掉**,可靠做法是写临时脚本文件执行。
4. **全量模式认证守卫无真实 MySQL 也可测**:注意 `is_password_set` 在「系统库不可达」时**故意返回 False**(放行重新引导,防死锁)——要模拟「已设密码」必须 monkeypatch,不能只切 run_mode。
5. **无系统 pip 时读 requirements.txt 会因 GBK 编码解码炸**(文件含 UTF-8 中文注释):直接按包名 `pip install pymysql cryptography` 可绕过。
6. 待办:SQL 查询执行器仍未立项(见 HANDOFF 待办表),是当前最大功能缺口。

---

## 二十四、前端测试依赖固化 + 离线单测 + CI 三级流水线 + 前端渐进模块化(2026-08-28)

> 背景:本机 Node v24/npm 11 可用,jsdom 此前靠外部 NODE_PATH 注入(小债)。对照外部 AI 给出的三条架构建议(前端模块化/测试体系增强/UX 优化),用户确认:暂不考虑移动端;执行 P0–P2,改动留在本机,经用户实测后再同步 git。

### 24.1 P0–P1 测试体系增强
- **package.json + jsdom 固化**:`devDependencies: jsdom ^26.1`;`npm test` 串起 4 套 jsdom 回归(test_frontend / db_picker / um_preset_all / update_log);4 个测试文件去掉 `require(path.join(process.env.NODE_PATH||"","jsdom"))`,改 `require("jsdom")`;`node_modules/` 入 .gitignore,`package-lock.json` 入库供 `npm ci`。本地 `npm install` + `npm test` 全绿。
- **离线单元测试 tests/test_units.py(30 项,零依赖)**:backup_engine 白名单 resolve_backup_file(穿越/敏感路径/后缀)/`_safe_filename`/`_gz_uncompressed_size`(构造真实 gzip 验证 ISIZE)/`_cli_args`(隔离目录+假客户端文件);env_probe find_tool/parse_version;schedule_store is_due(日/周 tm_wday 映射/月/一次性/禁用开关)/describe/save_task 校验与钳制(keep 99)/CRUD+MVC 旧配置迁移;local_store CRUD+clear_lite_data(保留最小 bootstrap)+reset_all;config_store Fernet 往返/pbkdf2/lite 管理员/默认键补齐;mysql_client 用 mock pymysql(test 成功路径/db_exists SQL 断言/真实连接拒绝→DbError 归一/_q/_q1)。
- **裸 confirm 收敛**:app.js 1795 行「切换全量模式」确认改走 confirmDialog(全项目统一 Promise 确认框)。
- **CI(.github/workflows/ci.yml)三级 job**:
  - backend(矩阵 3.10/3.11/3.12):py_compile + test_api + test_units;
  - frontend(Node 20):npm ci + npm test;
  - e2e(MySQL 8 服务容器 + mysql-client-8.0):MC_DATA_DIR 隔离数据目录准备激活连接 → 起服务 → test_e2e + test_progress 备份还原闭环。
- **test_e2e.py 异步化修正**:旧脚本仍按 R7 前的同步语义直接读 `r['result']`(对当前 202+task_id 接口必 KeyError);改为启动任务 → 轮询 /api/task/<id> 到终态再读 result。

### 24.2 P2 前端渐进模块化(不整 ES Module)
- app.js 头部改「文件头说明 + 目录索引(25 分区清单 + 开发规范)」;
- 工具命名空间:`const MCUtils = {fmtSize, fmtTime, esc}` + `window.MCUtils`(原函数声明保留,调用点零改动,供测试/后续拆分直接取用);
- `api()` / `confirmDialog()` 补 JSDoc(title/body/返回值);
- **明确不做**:ES Module 一步重构——jsdom 按普通 <script> 加载、inline onclick 依赖 window.* 桥(13 处)、零构建分发是硬约束,收益与风险不对等(DEVLOG R1/R9 教训)。

### 24.3 验证记录
- `node --check static/app.js` 通过;app.js 保持**纯 CRLF(2037/0)**;
- `npm test`:4 套 jsdom 回归全绿(test_frontend 27 项/db_picker 11 项/um_preset ALL PASS/update_log ALL PASS,EXIT 0);
- py_compile 全模块通过;`tests/test_api.py` 18/18;`tests/test_units.py` 30/30;
- e2e 闭环不本机连真实 MySQL(避免碰生产库),由 CI 首次触发时验证。

### 24.4 经验
1. **GitHub Actions 后台进程坑**:step 内 `python server.py &` 在该 step 结束时被 shell 回收——起服务与跑测试必须合并到同一 step(trap "kill $PID" EXIT 兜底)。
2. **文档引用的老测试会漂移**:test_e2e 停在 R7 异步化之前的同步语义,长期未跑必 KeyError;CI 引入后这类漂移会自动暴露。
3. **CI e2e 必须用 MySQL 8 客户端**:`--set-gtid-purged=OFF` 是 MySQL 8 参数,Ubuntu 默认 default-mysql-client 是 MariaDB 会报错;须 `apt-get install mysql-client-8.0` 并在 e2e 前 `mysqldump --version` 校验。
4. **npm ci 依赖 package-lock.json 入库**;package.json/lock 仅开发依赖,node_modules 永不入库(运行时/发布仍零构建)。
5. **P2 渐进路线(注释/命名空间/JSDoc)全部是零行为改动**,jsdom 回归就是安全网;真正拆分文件必须同步改 jsdom 测试与 window.* 桥,才可动。

---

## 二十五、用户授权查看 bug 修复 + root 授权保护 + 更新日志重复显示修复 + server 平台守卫(2026-08-28)

> 用户实测上报 2 个 bug + 1 项改进,本轮全部修复并补回归。

### 25.1 bug1:查看授权报「非法的用户标识」
- **现象**:「用户与连接 → 用户管理」点「查看授权」,任意用户都 404「非法的用户标识」(右上角提示)。
- **根因**:server.py `_parse_user_path` 先判断 `"@" in uh`、**后** `unquote`;前端 `encodeURIComponent("user@host")` 会把 `@` 编码成 `%40`——解码前的字符串里根本没有 `@`,于是**所有用户标识都被判非法**。影响查看授权/设置权限/改密/删除全部四个入口。
- **修复**:调换顺序——先 `unquote` 再判断 `@`(未编码的旧调用形式仍兼容)。
- **验证**:test_api 新增 test_18(编码/未编码/真正非法三种解析往返)与 test_19(编码路径返回可读 400「未激活连接」,不再 404)。

### 25.2 bug2:root 授权显示为空 + 需求确认(查看=只读,修改受保护)
- **现象澄清**:root「查看授权」为空是 bug1 404 的误读;「设置权限」弹窗本来就空勾选(编辑从空开始、保存即覆盖的设计)。
- **需求落地**:查看授权=只读列表(普通用户与 root 都列出);设置权限=仅普通用户可改;root 修改授权→提示「不允许修改」并给出建议。
- **修复**:
  - 后端:PET `/api/users/<u>@<h>` 的编辑授权分支对 `user.lower()=="root"` 返回 **403** 可读提示(改密码分支不受影响)——授权双端拦截,防绕过;
  - 前端:`openUserGrantsModal` 对 root 先 `confirmDialog` 提示后直接 return,不打开编辑弹窗;普通用户照常;
  - 编辑弹窗顶部加提示:「编辑授权从空勾选开始,保存覆盖现有授权;建议先点「查看授权」;root 授权不允许修改」。
- **验证**:新增 jsdom 回归 tests/test_um_root_guard.js(5 断言:root 提示打开/编辑弹窗未打开/标题正确/可关闭/普通用户编辑弹窗正常,ALL PASS);后端 403 分支需真实 MySQL 才可触达,接入数据库后人工复核。

### 25.3 改进1:更新日志重复显示
- **现象**:有新版时「最新版本更新日志」显示两次——上方 `#up-latest-log`(无条件展示)与更新动作区 `#up-changelog`(有新版时再展示一次)。
- **修复**:删除 index.html 的 `#up-changelog` 元素;loadUpdatePanel 与 checkUpdateNow 均不再向其填充——同一内容**单点输出**,只保留 `#up-latest-log`。
- **验证**:test_update_log 新增断言「#up-changelog 已移除」,四场景全绿。

### 25.4 server 平台守卫(排查顺带发现并修复)
- **现象**:`server.py` 模块级 `ctypes.windll.*` 绑定**无平台守卫**,Linux/macOS 下 `import server` 即 AttributeError——意味着 CI ubuntu 后端 job 无法跑,且 PLAN_v3「Linux 部署」声明实际不成立。
- **修复**:两段 Win32 绑定(GetOpenFileNameW 等 8 个 + SetWindowPos)包进 `if os.name == "nt":`;非 Windows 下原生对话框按既有 `_native_dialog` 的 nt 分支自动降级。
- **验证**:本机冒烟启动 health 200(Windows 行为不受影响);py_compile 通过;行尾保持 CRLF(server.py 1442/0)。

### 25.5 验证记录与经验
- **回归**:py_compile 全模块通过;test_api **20/20**;test_units **30/30**;npm test **5 套 ALL PASS**(test_frontend / db_picker / um_preset / **um_root(新)** / update_log)。
- 行尾:app.js CRLF 2044/0、index.html 884/0、server.py 1442/0(全部无损)。
- **经验**:
  1. percent-encoding 下「先判断后解码」是隐性 bug——URL 含保留字符(`@`/`%`)时必须**先 unquote 再判断**,并保留未编码调用兼容;
  2. root 超级管理员的授权保护要**前后端双端拦截**(前端 UX 提示 + 后端 403),仅做前端可被直接调 API 绕过;
  3. 「无条件展示」与「条件展示」共用同一内容时,必须**单点输出**,否则必然重复。

### 25.6 设置权限弹窗带出现有授权(2026-08-28 第二轮需求)
- **需求**:「用户与连接 → 设置权限」打开时应**带出该用户现有授权**,基于现状修改调整,而非从空重设。
- **实现**(纯前端,复用现有 `/api/users/<u>@<h>/grants` 接口):
  - 新增 `parseGrants(lines)`:`SHOW GRANTS` 文本 → `{scopeAll, databases[], privileges[], extra[]}`;
  - 新增 `loadCurrentGrantsIntoModal(user, host)`:编辑弹窗打开时拉取授权并回填——范围 radio(全部/指定)、指定库多选选中、权限网格勾选;加载完成后再显示弹窗;
  - `parseGrants` 挂 `window.parseGrants` 全局桥(与 window.* 桥约定一致,供 jsdom 回归取用)。
- **关键规则**:
  - **纯 `GRANT USAGE ON *.*` 占位行不参与归集**(MySQL 对每个用户默认输出该行)——否则任何指定库授权都会被误判为全局授权;
  - `ALL / ALL PRIVILEGES` → 全局 + 网格全选 + GRANT OPTION;
  - 表级/列级授权与界面网格外的系统权限(PROCESS/SUPER/RELOAD 等)→ `extra`,状态区提示「保存后将被本次勾选覆盖」;
  - 全局+指定库并存时按全局展示并提示;加载失败提示并保持空勾选。
- **验证**:新增 `tests/test_um_grants_prefill.js`(6 项解析断言 + 8 项 UI 回填断言);npm test 扩至 **6 套全部通过**(71 OK / 0 FAIL);app.js CRLF 2127/0 无损;后端 test_api 20/20、test_units 30/30 复测通过。
- **经验**:① SHOW GRANTS 的 USAGE 占位行是「带出权限」最大的坑,不跳过会把指定库授权误读为全局;② 界面外系统权限必须显式提示「保存将覆盖」,否则用户保存后会无感知丢失 PROCESS/SUPER 等权限。

### 24.5 启停脚本加固:start.bat/install.bat 加 `set PYTHONUTF8=1`(2026-08-28,用户实测触发)
- **现象**:用户本机 `start.bat` 报「Missing deps, installing requirements.txt ...」后 `No module named pip` → server.py 导入 cryptography 失败退出。
- **根因双连**:
  1. 工作区残留一个**缺 pip 的残缺 .venv**(早前开发环境创建失败遗留,`start.bat` 优先选用它,自装依赖分支 `pip` 不可用);
  2. 即使 venv 正常,pip 读取 requirements.txt(含 UTF-8 中文注释)时按 **GBK** 解析 → `UnicodeDecodeError`(本机控制台无 PYTHONUTF8 时必然复现,即 23.7-5 同源坑)。
- **修复**:
  - 重建 `.venv`(`python -m venv` 在完整权限下成功,venv pip 24.0 就位,`pip install -r requirements.txt` 通过)——残留残缺 venv 已清除;
  - `install.bat`(45 行前)与 `start.bat`(39 行前)在 pip 调用前加 `set PYTHONUTF8=1`(纯 ASCII,CRLF 保持 65/46),使**任何控制台代码页**下 pip 均按 UTF-8 读 requirements.txt,自装依赖不再受 GBK 解码影响。
- **验证**:按真实流程 `cmd /c "echo.|start.bat"` 冒烟——[1/3] 杀旧实例 → [2/3] 用 .venv → [3/3] 依赖检查通过(不再 Missing deps)→ 服务启动 → `/api/health` 200 `{"ok": true}`;测试后停 8090 进程,install.bat CRLF=65/0、start.bat CRLF=46/0 行尾无损。
- **经验**:启停/安装脚本的「自动装依赖」分支必须显式 `set PYTHONUTF8=1`,这是 Windows GBK 控制台 + UTF-8 中文注释配置文件的通用雷区,与项目原「.bat 纯 ASCII」铁律互不影响。

### 26. 目录结构化重构 + 发布打包(2026-08-28)
- **动因**:用户反馈 git 发布版整个目录混乱——24 个平铺 .py、8 份文档、10 个启停/安装脚本、前端 static/、测试、data/.venv/node_modules/_pydeps 全部混在根目录,「源码与工程文件放在一起」。
- **目标布局**(开发仓库与发布包一致):
  ```
  mysql-console/
  ├── src/             全部 Python 源码 + static/(前端)   ← 此前平铺在根目录
  │   ├── paths.py     新增:统一解析 APP_ROOT / DATA_DIR / STATIC_DIR
  │   ├── server.py    入口不变;BASE_DIR 改为部署根语义(APP_ROOT)
  │   └── static/      index.html / app.js / style.css / login.html / echarts.min.js
  ├── docs/            README、INSTALL、RELEASE、MIGRATION、DEVLOG、HANDOFF、PLAN_v3、MANIFEST
  ├── scripts/         install/start/stop/init(.bat/.sh) + mysql-console.service + _kill*.ps1
  ├── tests/           api/ unit/ e2e/ frontend/ 分型存放
  ├── requirements.txt / README.md / LICENSE(新增) / pyproject.toml(新增)
  └── data/            运行时数据(仍在部署根,不入库;MC_DATA_DIR 可重定位)
  ```
- **关键设计**:
  - `src/paths.py` 为路径单一来源:部署根 = src/ 的父目录(旧平铺布局、发布包、pip 安装三态兼容;pip 形态数据默认 `~/.mysql-console`);
  - 模块间 import **零改动**(src/ 平铺,裸 import 天然成立);只需把启动脚本/测试/CI 的模块搜索路径指向 `src/`;
  - 自更新(updater.py)替换范围由「根」自动变为「src/」(代码+static),顶层 data/.venv/node_modules 保留目录天然不动;新增 `_normalize_staging_src` 兼容新发布包(包内 src/ 子目录)与旧平铺包;
  - 启动/安装脚本做「部署根自动定位」:同一份脚本在 scripts/ 或发布包根下均可运行。
- **打包**:新增 `scripts/build_release.py` 一键产出 `dist/mysql-console-X.Y.Z.zip|.tar.gz`(替代手工 git archive,含自动校验);新增 `scripts/regen_manifest.py` 再生 `docs/MANIFEST.txt`;新增 `pyproject.toml` 支持 `pip install .` 后以 `mysql-console` 命令启动。
- **验证**:compileall 全模块通过;test_api 20/20、test_units 30/30、npm test 6 套前端回归全部通过(路径已适配 src/);E2E 需真实 MySQL 环境,由 CI 覆盖。
- **经验**:git mv 的目标目录必须预先创建;Windows PowerShell 5.1 不支持 `&&`;路径重构的三处回归要点是「入口脚本 / 测试 sys.path / CI 路径」同步更新。
