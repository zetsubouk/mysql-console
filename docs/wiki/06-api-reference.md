# 06 · REST API 全量地图

> 服务监听 `http://127.0.0.1:8090`（`MC_HOST`/`MC_PORT` 可覆盖）。所有响应为 JSON。
> **认证图例**：🔓 = 免登录白名单（7 条，[handlers.py:62](../../src/handlers.py#L62)）；🔒 = 需 Bearer 会话。绑定非回环地址时所有接口（除 `/api/security/info`）另需 `X-Access-Token` 头。POST/PUT/DELETE 额外经 CSRF Origin 同源校验。
> 路由源码：[routes.py](../../src/routes.py)（GET/POST 声明式表）+ [server.py do_PUT/do_DELETE](../../src/server.py#L183-L262)（内联）。

## 1. 系统与健康

| 方法 | 路径 | 认证 | 处理器 | 说明 |
|---|---|---|---|---|
| GET | `/api/health` | 🔓 | `g_health` | 存活探针 `{"ok":true}` |
| GET | `/api/version` | 🔓 | `g_version` | 版本 + platform 归一 |
| GET | `/api/auth-status` | 🔓 | `g_auth_status` | 是否已设管理员密码/登录态 |
| GET | `/api/security/info` | 🔓（令牌门豁免） | `g_security_info` | 回环判定/是否需要访问令牌 |
| GET | `/api/sys-resource` | 🔒 | `g_sys_resource` | 本机 CPU/内存/磁盘（psutil 可选增强） |

## 2. 认证与账号

| 方法 | 路径 | 认证 | 处理器 | 说明 |
|---|---|---|---|---|
| POST | `/api/login` | 🔓 | `_handle_login` | 登录；失败 5 次锁定 423；系统库不可达 503 |
| POST | `/api/logout` | 🔒 | `_handle_logout` | 注销当前会话 |
| POST | `/api/request-reset-code` | 🔓 | `_handle_request_reset_code` | 生成 6 位码（**打印到服务端终端**，10 分钟有效） |
| POST | `/api/reset-password` | 🔓 | `_handle_reset_password` | 验证码 + 新密码 |
| POST | `/api/change-password` | 🔒 | `_handle_change_password` | 修改密码 |
| POST | `/api/change-username` | 🔒 | `_handle_change_username` | 修改用户名 |
| POST | `/api/switch-to-full-mode` | 🔒 | `_handle_switch_to_full_mode` | lite→full **不可逆**切换 |

## 3. 连接管理（被控 MySQL）

| 方法 | 路径 | 认证 | 处理器 | 说明 |
|---|---|---|---|---|
| GET | `/api/connections` | 🔒 | `g_connections` | 列表（`has_password` 不回明文） |
| POST | `/api/connections` | 🔒 | `p_connections` | 新建（密码 Fernet 落库） |
| PUT | `/api/connections/<id>` | 🔒 | 内联 | 更新（密码空则不覆盖） |
| DELETE | `/api/connections/<id>` | 🔒 | 内联 | 删除 |
| POST | `/api/connect` | 🔒 | `p_connect` | 激活连接（内存 + 持久化双写） |
| POST | `/api/connections/test` | 🔒 | `p_connections_test` | 测试连通（`SELECT VERSION()`） |
| POST | `/api/connections/remote-check` | 🔒 | `p_connections_remote_check` | 远程存储/SSH 可达性预检 |

## 4. 监控与运维（作用于激活连接）

| 方法 | 路径 | 认证 | 说明 |
|---|---|---|---|
| GET | `/api/overview` | 🔒 | 服务器总览（版本/uptime/QPS/InnoDB…） |
| GET | `/api/databases` | 🔒 | 库列表（排除系统库，含大小/字符集） |
| GET | `/api/databases/<name>` | 🔒 | 库内表清单 |
| GET | `/api/users` | 🔒 | 被控 MySQL 用户列表 + 权限计数 |
| GET | `/api/users/<u>@<h>` | 🔒 | 用户详情 |
| GET | `/api/users/<u>@<h>/grants` | 🔒 | `SHOW GRANTS`（前端授权回填数据源） |
| POST | `/api/users` | 🔒 | 创建 MySQL 用户 |
| PUT | `/api/users/<u>@<h>` | 🔒 | 修改用户（授权/密码；root 授权后端 403 保护） |
| DELETE | `/api/users/<u>@<h>` | 🔒 | 删除用户 |
| GET | `/api/processlist` | 🔒 | 进程列表（Info 截 200 字） |
| POST | `/api/kill` | 🔒 | Kill 连接/查询 |
| GET | `/api/monitor` | 🔒 | 轻量增量监控（QPS 两次采样） |
| GET | `/api/monitor/full` | 🔒 | 深度监控（+InnoDB/复制状态） |
| GET | `/api/service/status` | 🔒 | 本机 MySQL 服务状态（远程 unknown） |
| POST | `/api/service/restart` | 🔒 | 重启本机 MySQL 服务（90s 就绪轮询） |

## 5. 数据看板 / 告警 / 变量

| 方法 | 路径 | 认证 | 说明 |
|---|---|---|---|
| GET | `/api/dashboard/health` | 🔒 | 健康评分（0-100 + 四分项） |
| GET | `/api/dashboard/health-history?hours=N` | 🔒 | 健康分历史（1-168h，分钟→小时聚合） |
| GET | `/api/dashboard/innodb` | 🔒 | InnoDB 命中率/脏页/锁等待/读写吞吐 |
| GET | `/api/dashboard/tablespace` | 🔒 | 表空间 Top（按库联动过滤） |
| GET | `/api/dashboard/replication` | 🔒 | 主从复制状态（5.7~8.4 兼容） |
| GET | `/api/alerts` | 🔒 | 阈值告警（warning/critical，阈值取 settings） |
| GET | `/api/alerts/history?days=N` | 🔒 | 告警历史（1-7 天采样） |
| GET | `/api/variables` | 🔒 | `SHOW VARIABLES` + 离线中文含义 |

## 6. 备份与还原（核心）

| 方法 | 路径 | 认证 | 说明 |
|---|---|---|---|
| POST | `/api/backup` | 🔒 | 发起备份 → **202 + task_id**（异步；gzip；本地/远程自动判定） |
| POST | `/api/restore` | 🔒 | 发起还原 → **202 + task_id**；`target_db` + 文件无建库语句时自动补建库 |
| GET | `/api/task/<tid>` | 🔒 | 轮询任务进度（percent/current 表名/message/elapsed） |
| GET | `/api/backups` | 🔒 | 备份历史（限 300 倒序） |
| DELETE | `/api/backups/<rid>` | 🔒 | 删除记录 + 白名单校验后删文件 |
| GET | `/api/backup-params` | 🔒 | mysqldump 可调参数（backup_opts/restore_opts） |
| GET | `/api/backup-files` | 🔒 | 备份目录文件浏览器 |
| GET | `/api/backup-files/download?file=` | 🔒 | 下载（**路径白名单防任意文件读取**；远程文件置灰） |
| POST | `/api/backup-files/remote` | 🔒 | 远程备份目录浏览（SSH） |
| POST | `/api/dialog` | 🔒 | 原生文件/目录对话框（Win32/osascript/zenity，600s 超时） |
| POST | `/api/browse` | 🔒 | 服务端目录浏览 |

## 7. 定时备份

| 方法 | 路径 | 认证 | 说明 |
|---|---|---|---|
| GET | `/api/schedules` | 🔒 | 任务列表 |
| POST | `/api/schedules` | 🔒 | 新建任务（默认启用） |
| PUT | `/api/schedules/<id>` | 🔒 | 更新（native→builtin 自动反注册系统计划任务） |
| DELETE | `/api/schedules/<id>` | 🔒 | 删除（native 先反注册） |
| GET | `/api/schedules/<id>` | 🔒 | 任务详情 |
| POST | `/api/schedules/toggle` | 🔒 | 启用/停用 |
| POST | `/api/schedules/register` | 🔒 | 注册到系统计划任务（schtasks/crontab + 自包含脚本） |
| POST | `/api/schedules/unregister` | 🔒 | 反注册（幂等） |
| GET | `/api/schedules/env` | 🔒 | 调度环境（平台/注册能力探测） |
| POST | `/api/schedule` | 🔒 | 旧版单任务入口（兼容保留） |

## 8. SQL 查询（只读）

| 方法 | 路径 | 认证 | 说明 |
|---|---|---|---|
| POST | `/api/query` | 🔒 | 只读 SELECT/SHOW/DESC/EXPLAIN/WITH；守卫拦截可执行注释/WITH+DML/SET GLOBAL/多语句；默认 500 行截断；`database=` 指定库 |
| POST | `/api/query/kill` | 🔒 | `{pid}` 中止执行中的查询（`KILL QUERY`） |

## 9. 引导向导 / 设置 / 日志

| 方法 | 路径 | 认证 | 说明 |
|---|---|---|---|
| GET | `/api/setup/env` | 🔒 | 环境汇总（Python/依赖/双工具状态） |
| POST | `/api/setup/probe-client` | 🔒 | 验证用户手填客户端目录（显式路径不存在直接报错） |
| POST | `/api/setup/test-db` | 🔒 | 向导测试数据库连接 |
| POST | `/api/setup/db-check` | 🔒 | 全量模式系统库预检 |
| POST | `/api/setup/drop-db` | 🔒 | 清理遗留系统库（标识符校验） |
| POST | `/api/setup/finish` | 🔒 | 完成向导（分 lite/full 初始化） |
| POST | `/api/setup/download-tools` | 🔒 | 后台下载双版本 MySQL 客户端（4 源 + SHA256） |
| GET | `/api/setup/download-tools/status` | 🔒 | 下载进度轮询 |
| GET | `/api/setup/db-detect` | 🔒 | 本机数据库三分量检测（OS 服务/mysqld 二进制/3306 端口 MySQL 握手），探测失败降级不报错 |
| GET | `/api/setup/mysql-versions` | 🔒 | MySQL Server 可安装版本（24h 缓存 → 官方 API+CDN 验证 → 内置兜底，永不报错/永不为空） |
| POST | `/api/setup/mysql-suggestions` | 🔒 | 安装参数建议 + 路径预检 + my.ini 预览（纯计算无副作用；内存缺省用本机探测值） |
| POST | `/api/setup/install-mysql` | 🔒 | 后台安装编排（下载→解压→my.ini→initialize→启动→设密码→可选服务注册；请求键白名单，`force` 为非空目录显式确认） |
| GET | `/api/setup/install-mysql/status` | 🔒 | 安装进度轮询（阶段/百分比/警告/连接信息） |
| GET | `/api/settings` | 🔒 | 读设置（DEFAULT_SETTINGS 26 键兜底） |
| PUT | `/api/settings` | 🔒 | 写设置（白名单过滤；`access_token` 单独 Fernet 加密） |
| GET | `/api/logs` | 🔒 | 操作日志（full 模式 mc_operation_log） |

## 10. 软件自更新 / AI

| 方法 | 路径 | 认证 | 说明 |
|---|---|---|---|
| GET | `/api/update/check` | 🔒 | 立即检查（GitHub → 降级缓存） |
| GET | `/api/update/badge` | 🔒 | 角标状态（有无新版） |
| GET | `/api/update/status` | 🔒 | update.log 尾部 50 行 |
| POST | `/api/update/prepare` | 🔒 | 下载+校验+备份（.part → staging → data/updates/backup） |
| POST | `/api/update/apply` | 🔒 | 起独立进程原子换码并重启服务 |
| GET/POST | `/api/ai/config` | 🔒 | AI 配置读（脱敏）/写（api_key Fernet 加密） |
| POST | `/api/ai/sql-gen` | 🔒 | 自然语言 → 只读 SQL（prompt 白名单约束） |
| POST | `/api/ai/sql-analyze` | 🔒 | EXPLAIN 优化建议 |
| POST | `/api/ai/report` | 🔒 | 告警/健康周报（metrics 采样为上下文） |
| POST | `/api/ai/test` | 🔒 | 显式参数连通性测试（不读已存配置） |

## 11. 统一约定

- **错误格式**：`{"ok": false, "error": "..."}`；`DbError` → 400 业务可读；未捕获异常 → 500「服务器错误」；系统库不可达 → 503「系统库不可用」。
- **异步任务**：备份/还原/工具下载返回任务标识，前端轮询对应 status 接口；任务快照在内存，服务重启后丢失。
- **激活连接**：监控/看板/备份等接口作用于当前激活连接（`POST /api/connect` 切换）。
