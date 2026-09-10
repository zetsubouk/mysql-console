# 加固与优化执行计划(批次一至六)

> 建立日期:2026-09-10(全仓三路扫描后与所有者确认的六批任务)。
> **状态:批次一至四已完成并推送(2026-09-10,见 DEVLOG 第四十一/四十二章);批次五、六待执行。**
> 本文自包含:每项含改动点、位置线索(函数/标识符为主,行号会漂移)、验收标准,
> 供任意环境/接手者(含 AI Agent)直接继续执行,无需原始会话上下文。

## 执行约定(每批必守)

- 每批完成后跑 HANDOFF §6 全套回归:`python3 -m py_compile`(全模块)→
  `python3 tests/unit/test_units.py`(135+ 项)→ `python3 tests/api/test_api.py`(38+ 项)→
  `npm test`(6 套 jsdom + vitest)→ 隔离 `MC_DATA_DIR` 实启动 + `/api/health` 冒烟。
- DEVLOG 按「改动清单 + 验证记录 + 经验」格式追加章节;wiki 01 §11 / 06 API / HANDOFF 同步。
- 已知门禁:Mimosa 写入钩子对**整文件 Write** 会误报既有写法(jsdom `window.eval`、
  `path.join(__dirname, "..")`、`Popen(cmd,...)` 等)——改用**最小 diff 的 Edit**
  (old/new_string 不包含既有敏感行)即可落地;`git commit` 若被拦,改动留工作区由所有者本地提交。
- 语法基线 Python 3.10(f-string 内嵌同类引号是 3.12+ 语法,严禁);bat 纯 ASCII + CRLF。

## 批次一:P0 快改 —— ✅ 已完成(2026-09-10)

1.1 updater 移除 TLS 降级 + assets 透传 digest ✅
1.2 login 页双 POST 修复 ✅
1.3 test_frontend.js 退出码修复(check() + failCount)✅
1.4 ci.yml 纳入 test_mysql_installer.py ✅
1.5 请求体 10MB 上限(413 + 断开)✅
1.6 max_rows 服务端钳制(mysql_client.clamp_query_rows,绝对上限 50000)✅

## 批次二:备份引擎"异常即清理"收口 —— ✅ 已完成(2026-09-10)

2.1 Popen 异常路径统一清理(terminate;仅 start_new_session 会话首可 killpg)✅
2.2 `_dump_to_file` 写线程异常上报 err_lines + wait 2s 轮询/30s 宽限强杀 ✅
2.3 任务取消:`POST /api/task/<id>/cancel` + 进度弹窗按钮(routes.py 补 POST 前缀分发)✅
2.4 失败任务清理半截产物(本地删除/多库 zip 全成才留/远端尽力 rm)✅
2.5 zip 还原流式化(shutil.copyfileobj)✅
2.6 备份前磁盘空间预检(多库 ×2 峰值)✅
2.7 SSH 隧道 stderr 常驻排空线程(err_buf 保留 20 行)✅

## 批次三:防滥用加固 —— ✅ 已完成(2026-09-10)

3.1 找回密码:发码 60s 节流(429)+ 未用码 ≤10 + 失败 5 次作废全部未用码;过期码修剪 ✅
3.2 安全响应头 `X-Content-Type-Options: nosniff` + `X-Frame-Options: DENY` ✅
3.3 `.secret.key` chmod 0600(生成/加载时收紧,Windows 跳过)✅
3.4 重新引导:先备份 config.db(+wal/shm)→ 失败整组回滚;旧系统库推迟到初始化成功后 drop ✅

---

## 批次四:前端健壮性与体验 —— ✅ 已完成(2026-09-10,DEVLOG §42)

- [x] **4.1 monitorLoop 条件轮询**(src/static/app.js,搜 `setInterval(monitorLoop, 5000)`):
  仅 overview 页可见且 `document.visibilityState === "visible"` 时轮询(参照数据看板页已有的
  visibilitychange 模式);连续失败 `updateConnStatus(false)` 翻红 + 指数退避;删除 `catch (e) {}` 静默。
  验收:切走页签/浏览器后台时 Network 面板无 `/api/monitor/full` 轮询。
- [x] **4.2 消除 dashboard-helpers.js 影子副本**:该 ESM 文件生产从未被 index.html 加载,
  vitest(tests/vitest/dashboard.test.js)测的是它,而 app.js 内 `_dashDownsample/_dashFormatUpdated/_dashStatus`
  是手抄副本——测试绿≠生产对。改法:副本逻辑收敛为一份(普通脚本挂 `window.DashHelpers`,
  在 app.js 之前加载),app.js 引用之,vitest 改用 jsdom eval 断言生产实现(test_frontend.js 同模式)。
- [x] **4.3 数据加载竞态守卫**:`showDbDetail`/`browseTo`/`loadRemoteRestoreFiles` 加模块级自增 seq
  (或 AbortController),写 DOM 前校验仍是最新请求;查询页签已有状态机,无需改。
- [x] **4.4 echarts 懒加载**:index.html 两个 `<script src=...>`(echarts.min.js ~1MB 与 app.js)加 `defer`
  (顺序保留,零构建兼容);`initCharts()` 延迟到首次进入 overview/dashboard 页,未登录/落其他页不建实例。
- [x] **4.5 错误处理补齐**:`loadConnections`/`removeConn`/`umLoadDbs` 加 try/catch + toast,
  与同文件其他 load* 一致(当前失败 = unhandled rejection,无任何提示)。
- [x] **4.6 小 UX 打包**:侧栏底部硬编码 "localhost:8090" 改 `location.host` 回填(index.html);
  head 加内联 `data:` SVG favicon(现状每页一条 404);变量页 `#var-filter` oninput 加 150ms debounce
  (当前每键全量重建 ~600 行 innerHTML);pollTask 500ms 改 1s 起步退避、`#a32d2d/#3b6d11` 硬编码颜色
  改 CSS 变量(`--danger/--success`,暗色主题适配);访问令牌 `window.prompt`(app.js api() 内)改复用
  modal 体系并加"正在输入"去重标志(多请求并发 401 会连弹多个 prompt)。

## 批次五:CI/工程门禁(待执行)

- [ ] **5.1 ruff lint job**:新增独立 lint job(`ruff check` 起步,只查不改);规则集(pyproject.toml
  `[tool.ruff]`)先给所有者过目再启用。现状 compileall 只查语法,未定义名/风格退化无门禁。
- [ ] **5.2 版本一致性门禁**:ci.yml 调 `python scripts/sync_version.py --check`(工具已存在,CI 从未调用);
  构建命令去掉硬编码 `--tag v3.8.1`(它覆盖 version.py,bump 后仍产出旧版本号包)改读 version.py;
  `build_release.validate()` 顺带断言包内 `src/version.py` 内容 == 构建版本。
- [ ] **5.3 ci.yml 三行加固**:顶层 `concurrency: {group: ci-${{ github.ref }}, cancel-in-progress: true}`
  (PR 期间重复 push 双跑全流水线);各 job `timeout-minutes: 15-25`(现默认 360 分钟,systemd/e2e 挂起
  最长烧 6 小时);顶层 `permissions: { contents: read }`(最小权限基线)。
- [ ] **5.4 MANIFEST 漂移检查**:CI 临时目录跑 `python scripts/regen_manifest.py` + diff `docs/MANIFEST.txt`,
  不一致即失败(发布自检材料,加文件/改内容后极易忘记重生成)。
- [ ] **5.5 覆盖率报告**:coverage.py(`coverage run -m unittest ...`)+ vitest coverage
  (vitest.config.js 已配 v8 coverage 但 npm test 从不开启);先出报告不设门槛,再逐步 `fail_under`。
- [ ] **5.6 补零触达测试**:`updater.download()` 失败分支(mock urlopen:大小不符/SHA 不符/空文件/
  .part 清理);`tools_downloader._verify_tmp/_extract_subdir`(整模块零测试);`MC_TLS=1` 起服务的
  真实握手 API 测试(不校验证书的 SSLContext 请求 /api/health 断言 200)。

## 批次六:P2 清理(待执行,可按项拆并)

- [ ] **6.1 会话清理**:`_clear_expired_sessions`(handlers.py,现为死代码)接入调度循环周期调用;
  `_sessions` 的 `del` 改 `pop(token, None)`(两请求同刻过期可 KeyError,且该异常在 do_GET try 之外直接断连)。
- [ ] **6.2 后台循环日志**:`_alert_history_loop`/`_update_loop` 的 `except Exception: pass` 改
  失败计数 + 每 N 次失败 print 一条摘要(持续故障零线索)。
- [ ] **6.3 自更新端口注入**:`updater.py` UPGRADER_TMPL 硬编码 `PORT = 8090`,改生成脚本时注入
  `repr(security.bind_port())`(自定义 MC_PORT 部署时 wait_port_free 立即通过,可能旧进程未退就换码)。
- [ ] **6.4 对话框线程泄漏**:`p_dialog` 的 `t.join(timeout=600)` 超时后旧线程抱着对话框继续活,
  反复触发可堆积——超时后终止对话框子进程(osascript/zenity 可 kill;Win32 发 WM_CLOSE)。
- [ ] **6.5 杂项**:`pip_bootstrap` mkdtemp 所有路径不清理(try/finally rmtree);裸 `except:` 改
  `except Exception`(handlers.py 两处 + ai_client.py 一处,吞 SystemExit/KeyboardInterrupt)。
- [ ] **6.6 小修**:静态前缀校验 `fp.startswith(STATIC_DIR)` 改 commonpath(server.py,兄弟目录绕过);
  `backup_engine.get_task` 快照 `detail=list(t["detail"])` 深拷贝(500ms 轮询下浅拷贝可撕裂)。
- [ ] **6.7 依赖治理**:requirements.txt 给 `cryptography>=42` 加上界(CI 是随机版本冒烟场);
  加 dependabot.yml(pip + npm 生态)+ 每周 `pip-audit`/`npm audit` 定时 job。

## 明确不做 / 另行立项(勿顺手做)

- 前端框架化 / ES Module 大重构(文档已定零构建路线);
- DPAPI 根治 native_script 明文密码(单机工具取舍,wiki §11 已记录);
- SSH paramiko 远程执行备份(可行性已论证,待所有者决策);
- MySQL 连接池化(P1,牵连 handlers/mysql_client/system_db 多模块,建议单独立项);
- 自带运行时收尾(`--with-runtime` 实际构建、私有 runtime 下 schtasks 真机触发,属既有路线图)。
