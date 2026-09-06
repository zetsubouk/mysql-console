# 08 · 核心时序图

> **阅读说明**：本页共 11 张时序图（sequence diagram），覆盖系统全部关键运行期交互。图表类型选择理由：描述"跨参与者消息顺序"时序图是最贴合的记法（备选：活动图，适合强调分支与并行，但会丢失参与者边界）。
> - **源格式**：Mermaid（源码即文档，随仓库版本化，可 diff）；
> - **渲染**：GitHub / GitLab / VS Code / Typora 原生渲染，无需插件；如需静态导出可用 `mmdc -i 08-sequence-diagrams.md -o out.svg`（mermaid-cli）；
> - **参与者命名**与源码模块一一对应，行号引用见 [03-classes-and-functions.md](03-classes-and-functions.md)。
> - **配套静态视图**：参与者背后的类/组件/部署结构见 [09-class-and-component-diagrams.md](09-class-and-component-diagrams.md)，其 §0 提供与本页 11 张图的对应关系表。

## 1. 一次 GET API 请求的生命周期

```mermaid
sequenceDiagram
    autonumber
    participant B as 浏览器 app.js
    participant S as Handler (server.py)
    participant G as _auth_guard
    participant R as routes.dispatch
    participant HB as HandlerBase 处理器
    participant MC as mysql_client

    B->>S: GET /api/monitor（Bearer + X-Access-Token 双头）
    S->>S: path 非 /api/ 前缀？否，继续
    S->>G: 三层守卫
    G->>G: ① 访问令牌门（非回环强制，常数时间比较）
    G->>G: ② Bearer 会话校验（8 小时）
    G-->>S: 通过
    S->>R: dispatch("GET", path)
    R->>R: 精确表 O(1) → 前缀表（按长度降序）
    R->>HB: getattr(self, "g_monitor")() 反射调用
    HB->>MC: monitor_metrics(激活连接)
    MC-->>HB: 连接数/QPS/慢查询
    HB-->>S: dict
    S-->>B: 200 JSON（no-store）
    Note over S: DbError 转 400 可读错误 / 未捕获异常转 500
```

> POST/PUT/DELETE 在第 4 步后额外经过 `_check_csrf()`（Origin/Host 同源校验）。

## 2. 登录与会话签发（三层安全的第一触点）

```mermaid
sequenceDiagram
    autonumber
    participant B as login.html
    participant S as Handler (do_POST)
    participant L as _handle_login
    participant CS as config_store
    participant SS as 内存 _sessions

    B->>S: POST /api/login（用户名 + 密码）
    Note over S: /api/login 在免认证白名单<br/>但仍过 CSRF 同源校验
    S->>L: body
    L->>CS: get_admin_lock_status / verify_admin
    alt 账号已锁定
        CS-->>L: locked_until 未过期
        L-->>B: 423 已锁定
    else 系统库不可达（full 模式）
        CS-->>L: SystemDbUnavailable
        L-->>B: 503 系统库不可用（不回退陈旧数据）
    else 密码错误
        L->>CS: update_admin_login_fail（计数 +1）
        L-->>B: 401
    else 验证通过
        L->>SS: token = secrets.token_hex(32)<br/>存（用户名，now+8h）
        L-->>B: ok + token
        B->>B: 保存 mc_token，后续请求带 Bearer
    end
```

> 找回密码旁路：`POST /api/request-reset-code` 将 6 位验证码**打印到服务端终端**（10 分钟有效、一次性），`POST /api/reset-password` 凭码重置。

## 3. 手动备份：异步任务 + 双维度进度

```mermaid
sequenceDiagram
    autonumber
    participant B as 浏览器 app.js
    participant A as POST /api/backup
    participant BE as backup_engine
    participant W as worker 守护线程
    participant MD as mysqldump 子进程
    participant IS as information_schema
    participant DS as 数据存储（SQLite/系统库）

    B->>A: 备份参数（库范围/gzip/目录）
    A->>BE: start_backup_task(...)
    BE-->>A: 202 + task_id（12 位 hex）
    A-->>B: 202 + task_id
    BE->>W: 启动 worker，进度回调绑定 task_id
    W->>IS: _prefetch_tables 预查表清单与大小
    alt 本地存储（host 为 localhost/127.0.0.1/::1）
        W->>MD: Popen mysqldump --verbose（stdout=PIPE）
        MD-->>W: stderr 逐行 for table X → 表级进度
        W->>W: stdout 1MB 分块 → gzip 落盘 → 字节进度
    else 远程存储（SSH 管道直写）
        W->>W: ssh mkdir -p 且 cat > path（stdin=PIPE）
        W->>MD: Popen mysqldump（stdout=PIPE）
        W->>W: 桥接 gzip.GzipFile(ssh_in) 客户端压缩，不落本地盘
        W->>W: 远端 gzip -dc 统计字节数校验实际大小
    end
    loop 前端轮询至 done/failed
        B->>A: GET /api/task/<tid>
        A-->>B: percent / current 表名 / message / elapsed
    end
    W->>DS: add_history 落备份历史
    W->>W: 按 keep 数清理旧备份
```

## 4. 还原：建库语句识别 + 自动补建库

```mermaid
sequenceDiagram
    autonumber
    participant B as 浏览器
    participant A as POST /api/restore
    participant BE as backup_engine
    participant F as 备份文件 .gz/.sql/.zip
    participant DB as 被控 MySQL
    participant MY as mysql 子进程

    B->>A: 备份文件 + target_db
    A->>BE: start_restore_task → 202 + task_id
    BE->>F: 读前 256KB，匹配 CREATE DATABASE 或 USE
    alt 不含建库语句 且 指定 target_db
        BE->>DB: CREATE DATABASE IF NOT EXISTS（utf8mb4）
    else 自带建库（--databases 模式 dump）
        Note over BE: 不传库名参数，由 dump 文件自管
    end
    BE->>MY: Popen mysql（stdin=PIPE，stderr 排空线程保根因）
    alt 本地 .gz
        BE->>F: gzip.open 流式解压
    else 本地 .zip
        BE->>F: 解临时目录（成员取 basename 防穿越）逐成员递归
    else 远程文件
        BE->>F: ssh 远端 gzip -dc 只读流
    end
    loop 1MB 分块直至 EOF
        F-->>MY: 写 stdin（进度分母取 .gz 尾部 ISIZE 字段）
    end
    MY-->>BE: wait() 退出码（断管错误与 mysql stderr 拼接）
    BE-->>B: 轮询 /api/task/<tid> 至 done/failed
```

## 5. 定时备份（内置引擎）：20 秒调度循环

```mermaid
sequenceDiagram
    autonumber
    participant T as scheduler_loop（每 20 秒）
    participant SS as schedule_store
    participant BE as backup_engine
    participant DS as 数据存储

    loop 每 20 秒
        T->>SS: list_tasks()
        SS-->>T: engine=builtin 且 enabled 的任务
        T->>SS: is_due(task, now)
        alt hourly
            Note over T: 恒命中，由 last_run 间隔判定<br/>容差 19 分钟对抗轮询粒度
        else daily/weekly/monthly/once
            Note over T: HH:MM 精确比对 + _last_fire 同分钟去重<br/>weekly 需 (tm_wday+1) % 7 平移（业务 0=周日）
        end
        T->>BE: run_backup（与手动备份同路径）
        BE-->>T: 结果
        T->>SS: 回写 last_run / last_result
        T->>DS: 按 keep 数清理旧备份
    end
```

## 6. 定时备份（原生引擎）：注册与 OS 到点执行

```mermaid
sequenceDiagram
    autonumber
    participant B as 浏览器
    participant A as /api/schedules/register
    participant NS as native_script
    participant SC as native_scheduler
    participant OS as schtasks / crontab
    participant SH as 自包含脚本 backup_id.ps1/.sh
    participant MD as mysqldump

    B->>A: task_id
    A->>NS: 渲染脚本（凭据/目录/KEEP/MIN_SIZE）
    NS-->>A: .ps1（UTF-8 BOM+CRLF）或 .sh（chmod 700）
    A->>SC: register(task)
    SC->>OS: schtasks /create /tn MySQLConsole_id /tr powershell 脚本<br/>或 crontab 追加 marker 行（幂等）
    OS-->>SC: 成功 → 回写 native_registered=True
    Note over OS: 到点由 OS 拉起脚本，不依赖本服务进程存活
    OS->>SH: 定时触发
    SH->>MD: 探测（配置目录→PATH）→ 导出 → MIN_SIZE 校验 → gzip → keep 清理 → 写日志
    Note over A: 注册失败时 gen_command 返回可手动执行的兜底命令
```

> 引擎一致性：任务 PUT 从 native 改回 builtin 时自动反注册；DELETE 任务前先反注册（[server.py do_PUT/do_DELETE](../../src/server.py#L183-L262)）。

## 7. SSH 隧道：3306 直连不通时的备份链路

```mermaid
sequenceDiagram
    autonumber
    participant BE as backup_engine
    participant TU as ssh_tunnel
    participant SSH as ssh 子进程
    participant JH as SSH 跳板机
    participant DB as 远程 MySQL 3306

    BE->>TU: is_ssh_cfg（ssh_enabled 且 ssh_host 非空）
    TU->>SSH: ssh -N -T -i 私钥 -L 127.0.0.1:空闲端口:bind_host:3306 user@跳板
    SSH->>JH: 建立隧道
    JH->>DB: 端口转发
    TU->>TU: 轮询本地端口就绪（8 秒超时）
    TU-->>BE: eff 端点改写为 127.0.0.1:空闲端口
    BE->>DB: mysqldump/mysql 经隧道执行备份/还原
    BE->>TU: stop_tunnel 幂等清理
    Note over BE: 存储位置判定用原始 host（storage_cfg）<br/>隧道化端点仅用于实际连接（eff）——双层参数签名的原因
```

## 8. 软件自更新：check → prepare → apply

```mermaid
sequenceDiagram
    autonumber
    participant B as 浏览器（更新面板）
    participant U as updater.py
    participant GH as GitHub Releases
    participant P as 独立进程 apply_update.py
    participant SRV as 运行中的服务

    B->>U: GET /api/update/check
    U->>GH: releases/latest
    alt 网络失败逐级降级
        U->>U: 免验证 SSL 重试 → 本地缓存 → bundled_release.json（offline）
    end
    U-->>B: 新版本信息
    B->>U: POST /api/update/prepare
    U->>GH: 下载资产（.part 临时文件 + 大小 + SHA256 强校验）
    U->>U: 解压归一（内层 src/ 提升顶层）→ 备份当前码 backup/版本/
    B->>U: POST /api/update/apply
    U->>P: 生成 apply_update.py 并以独立进程启动
    Note over U,SRV: 主进程退出（运行中进程无法替换自身 .py 且不能自重启）
    P->>SRV: wait_port_free（8090，最多 90×2 秒）
    P->>P: swap：删旧码（保留 data/.venv/node_modules 等）→ 拷新码
    P->>SRV: 按原方式重启（start.bat/start.sh/server.py）
    P->>P: 全程写 update.log（GET /api/update/status 回读尾部 50 行）
```

## 9. 首次运行三步向导

```mermaid
sequenceDiagram
    autonumber
    participant B as 浏览器向导
    participant A as /api/setup/*
    participant EP as env_probe
    participant TD as tools_downloader
    participant CS as config_store
    participant SDB as system_db

    B->>A: GET /api/setup/env
    A->>EP: env_summary()
    EP-->>B: Python/依赖/mysqldump/mysql 状态
    opt 客户端缺失（slim 版）
        B->>A: POST /api/setup/download-tools
        A->>TD: daemon 线程下载双版本（4 源逐试 + SHA256）
        loop 轮询
            B->>A: GET /api/setup/download-tools/status
        end
    end
    B->>A: POST /api/setup/probe-client（显式路径不存在直接报错）
    B->>A: POST /api/setup/test-db（连接测试）
    alt 轻量模式 lite
        B->>A: POST /api/setup/finish
        A->>CS: prepare_lite()
    else 全量模式 full
        B->>A: POST /api/setup/db-check → finish
        A->>SDB: init_system_db（建库 + 6 张 mc_* 表）
        A->>SDB: import_from_file（本地数据迁移）
        A->>CS: 写 bootstrap（密码 Fernet 加密）
    end
    A->>A: _ensure_runtime_scripts 生成 start/stop/init
```

## 10. lite → full 模式切换（不可逆）

```mermaid
sequenceDiagram
    autonumber
    participant B as 设置页
    participant A as POST /api/switch-to-full-mode
    participant CS as config_store
    participant LS as local_store
    participant SDB as system_db

    B->>A: sys_db_name + 管理员账号密码
    A->>CS: switch_to_full_mode()
    CS->>LS: 备份 config.db
    CS->>SDB: init_system_db（建库 6 表）
    CS->>SDB: import_from_file（连接/设置/任务/历史/日志）
    CS->>CS: set_admin（PBKDF2）+ 写 bootstrap（Fernet）
    CS->>LS: clear_lite_data（仅留 bootstrap 相关 meta）
    A-->>B: ok（操作不可逆）
    Note over CS: 此后 run_mode/sys_db_name 以本地 meta 为权威<br/>系统库不可达时登录回 503 而非回退陈旧数据
```

## 11. 只读 SQL 查询与中止

```mermaid
sequenceDiagram
    autonumber
    participant B as SQL 查询页（多页签）
    participant A as POST /api/query
    participant MC as mysql_client
    participant DB as 被控 MySQL
    participant W as 执行子线程

    B->>A: sql + database + max_rows
    A->>MC: _query_guard_error 快速守卫
    alt 命中写语句/可执行注释/WITH 接 DML/SET GLOBAL/多语句
        MC-->>B: 400 可读拒绝
    else 只读语句放行
        A->>W: 子线程执行 + join 同步等待
        W->>DB: 运行查询（默认 500 行截断）
        opt 用户中止
            B->>A: POST /api/query/kill（pid）
            A->>DB: KILL QUERY
        end
        W-->>A: columns/rows/truncated/elapsed
        A-->>B: 结果集（服务端日志只记 SQL 前 80 字符）
    end
```

---

## 附：时序图之间的关联

- 图 1 是所有交互的骨架（认证 + 路由分发），图 2/3/4/9/10/11 均复用该骨架；
- 图 3 与图 7 可叠加：远程备份 = 图 7 建隧道 + 图 3 的 SSH 直写分支；
- 图 5 与图 6 互斥：同一任务只会走 builtin（进程内）或 native（OS 调度）其中一条链路；
- 图 8 的 apply 阶段会重启服务，导致内存态（图 2 的会话、图 3 的任务快照）全部失效——这是已知的单机取舍（见 [01-architecture.md §11](01-architecture.md)）。
