# MySQL Console — Code Wiki

> **版本基准**：v3.8.1（[src/version.py](../../src/version.py)）｜ **文档生成日期**：2026-09-06
> 本 Wiki 由代码仓库静态分析生成，行号引用与当前源码一一对应。

## 一句话概述

**零框架的 MySQL 可视化管理平台**：Python 标准库 `http.server` 起服务（端口 8090）+ 原生 JS 单页前端（零构建），即可完成对**本机或任意远程** MySQL 的监控、备份/还原、定时备份、用户与权限管理全流程。部署机**无需安装 MySQL**，运行依赖仅 `pymysql` + `cryptography` 两个包。

## 关键数字

| 指标 | 数值 | 说明 |
|---|---|---|
| Python 源码 | 27 个模块，约 11,400 行 | 全部位于 [src/](../../src/)，平铺布局 |
| 前端代码 | app.js 3,314 行 + index.html 1,116 行 | 单文件 SPA，无构建步骤 |
| REST API | 78 条路由（39 GET + 39 POST）+ 4 组 PUT/DELETE | 声明式路由表 [src/routes.py](../../src/routes.py) |
| 运行时依赖 | 2 个第三方包 | `pymysql>=1.1,<2`、`cryptography>=42` |
| 后台线程 | 3 个守护线程 | 备份调度 20s / 告警采样 60s / 更新检查 1h |
| 系统库表 | 6 张 `mc_*` 表 | 全量模式存储于 MySQL `_mysql_console` 库 |
| 测试 | 5 类（unit/api/e2e/frontend/vitest） | CI 5 个 job，三级 + 跨平台 + systemd |
| 发布产物 | 4 包矩阵 | `--platform win64\|linux × --variant slim\|standard` |

## 文档导航

| 文档 | 内容 | 适合场景 |
|---|---|---|
| [01-architecture.md](01-architecture.md) | **整体架构**：分层结构、请求生命周期、线程模型、认证模型、双模式存储、设计取舍 | 理解系统全貌 |
| [02-modules.md](02-modules.md) | **主要模块职责**：27 个 Python 模块 + 前端 + 脚本逐个说明（含行数） | 定位代码位置 |
| [03-classes-and-functions.md](03-classes-and-functions.md) | **关键类与函数**：Handler/StorageBackend/main()/scheduler_loop/run_backup 等逐一解析 | 精读核心实现 |
| [04-dependencies.md](04-dependencies.md) | **依赖关系**：模块依赖图、循环依赖化解、第三方依赖、策略同步点 | 评估改动影响面 |
| [05-running.md](05-running.md) | **运行方式**：开发启动、安装脚本、环境变量、systemd、测试、构建发布、初始化、自更新 | 部署与日常操作 |
| [06-api-reference.md](06-api-reference.md) | **API 全量地图**：78+ 条路由按业务域分组，含认证要求与参数 | 前后端联调 |
| [07-testing-and-ci.md](07-testing-and-ci.md) | **测试与 CI**：五类测试说明、运行命令、CI 五 job 详解、避坑清单 | 改动后回归验证 |
| [08-sequence-diagrams.md](08-sequence-diagrams.md) | **核心时序图**（11 张 Mermaid）：请求生命周期、认证、备份/还原、双引擎调度、SSH 隧道、自更新、向导、模式切换、SQL 查询 | 理解运行期交互 |

## 快速开始（最短路径）

```bash
# ① 安装（Windows 用 platforms\win64\scripts\install.bat）
./platforms/linux/scripts/install.sh

# ② 启动（默认 http://127.0.0.1:8090）
./platforms/linux/scripts/start.sh

# ③ 浏览器打开 http://127.0.0.1:8090，完成三步向导：
#    环境检测 → MySQL 客户端目录 → 数据库连接
```

> 恢复出厂：`init.sh` / `init.bat`（删除全部配置、系统库与备份，**破坏性操作需确认**）。

## 相关文档（仓库自带）

- [docs/INSTALL.md](../INSTALL.md) — 部署指南（双平台 / FAQ）
- [docs/architecture.html](../architecture.html) — 交互式架构图（暗色 SVG）
- [docs/HANDOFF.md](../HANDOFF.md) — 开发者/AI 交接指南（血泪陷阱清单）
- [docs/DEVLOG.md](../DEVLOG.md) — 开发演进史
- [docs/PLAN_v3.md](../PLAN_v3.md) — V3 设计规划
