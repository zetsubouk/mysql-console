# MySQL Console v3 开发规划 —— 跨平台可安装部署版

> 状态:**一期、二期均已完成并通过验证(2026-08-24)** | 三期(可选)待确认
> 目标:**任何一台 Windows/Linux 主机**,拿到项目目录后即可安装部署;通过浏览器引导完成环境检测与配置;**不强制本机部署数据库**,被管理的 MySQL 可以是本机实例,也可以是独立服务器。
>
> 已完成前置修复:native_scheduler.py 的 Python 3.12+ f-string 语法(本机 3.11 编译失败)已修复并验证。

---

## 一、现状与目标的差距

| # | 差距 | 位置 | 影响 |
|---|---|---|---|
| G1 | mysqldump/mysql.exe 路径硬编码为固定客户端目录 | backup_engine.py / mysql_client.py | 非安装 MySQL 的环境(路径不可用)备份还原不可用 |
| G2 | 无首次运行引导:新主机用户不知道要配什么、哪里配 | 全局 | 部署门槛高,靠读文档 |
| G3 | start.bat 硬编码旧机 python 绝对路径 | start.bat | 直接双击无法启动 |
| G4 | 无 Linux 启动方式(仅 .bat),无服务化方案 | 项目根 | Linux 无法便捷部署 |
| G5 | "远程数据库"能力存在但不显性:UI 未表达、客户端/服务器版本兼容无校验 | 前端 + backup_engine | 用户不清楚支持远程 |
| G6 | 依赖需手动 pip 安装,无安装脚本 | 无 | 部署步骤多 |
| G7 | stop.bat 中文乱码(GBK/chcp 不一致) | stop.bat | 小问题,顺手修 |

> 注:服务本身已天然独立于数据库运行(HTTP 服务不依赖本地 MySQL 存在),G5 是"表达与校验"问题,非架构问题。

---

## 二、需求清单

### R1 环境自适配(P0,消除硬编码)

- **R1.1 MySQL 客户端路径动态化**
  - 新增全局设置项 `mysql_bin`(存入 config.json settings,网页设置页可改);
  - 解析顺序:用户设置值 → PATH(`shutil.which("mysqldump")`) → 常见目录自动扫描
    (Windows: 常用安装目录、`C:\Program Files\MySQL\*\bin`、XAMPP/phpstudy 等;Linux: `/usr/bin`、`/usr/local/mysql/bin`、`/opt/mysql*/bin`);
  - `backup_engine._cli_args()` / `mysql_client.MYSQL_BIN` 改为调用统一的 `resolve_mysql_bin()`;
  - 探测不到时:服务照常启动,备份/还原入口给出明确降级提示("未找到 MySQL 客户端,请在设置中指定"),不再隐性崩溃。
- **R1.2 Python 兼容基线**:代码全部按 **Python 3.10+** 兼容写法(禁用 3.12+ 专属语法);README 标注最低版本。
- **R1.3 启动/停止脚本跨平台**
  - `start.bat`:自动探测 python(py 启动器 → python → 常见安装路径),移除绝对路径;
  - 新增 `start.sh` + `stop.sh`(Linux/macOS,bash);
  - 修复 stop.bat 编码(统一 UTF-8 无 BOM 或纯英文提示)。

### R2 首次运行引导向导(P0 核心,新功能)

- **触发条件**:首次启动(无任何连接配置)或 `settings.setup_done != true` 时,前端自动进入引导模式;已配置用户不受任何影响。
- **向导三步**(Web 弹层式,复用现有 UI 风格):
  1. **环境检测**:表格列出 Python 版本、pymysql/cryptography、MySQL 客户端(mysqldump/mysql.exe)、磁盘写权限,逐项 ✓/✗ 与修复建议;
  2. **客户端与备份目录**:MySQL 客户端路径(带"自动探测"按钮 + 手动填写 + "验证"按钮,实际执行 `mysqldump --version`);备份目录默认 `./data/backups`,可改;
  3. **数据库连接**:名称/host/port/user/password,**页面文案明确"本机或远程服务器均可"**;"测试连接"实时反馈(含服务器版本号);成功后保存并激活,写入 `setup_done=true`,进入主界面。
- **后端新增 API**(挂在现有 Handler,风格一致):
  - `GET /api/setup/env` — 汇总环境探测结果;
  - `POST /api/setup/probe-client` — 按 path 探测并返回 mysqldump 版本;
  - `POST /api/setup/test-db` — 复用现有 `mysql_client.test`;
  - `POST /api/setup/finish` — 保存 mysql_bin 设置 + 连接 + setup_done 标志(一个事务性接口)。
- 连接管理页保留"重新运行引导"入口。

### R3 远程数据库一等公民(P1)

- 连接表单/列表增加「本机 / 远程」徽标(按 host 判断,纯展示);
- 备份/还原前校验:客户端工具版本 vs 目标服务器版本大版本不一致时弹黄色警告(如用 8.x dump 导 5.7,经典坑);
- 还原/备份历史记录补充目标 host 信息,便于区分多套环境。

### R4 部署形态(P1)

- **一键安装脚本**:
  - `install.bat`(Windows):检测 python → 建 venv → `pip install -r requirements.txt` → 提示运行 start.bat;
  - `install.sh`(Linux):同逻辑 + 可选 `--service` 参数;
- **Linux 服务化**:`scripts/mysql-console.service` systemd 模板,`install.sh --service` 自动渲染 User/WorkingDirectory 并 enable;
- Windows 服务化本期不做注册器,README 给出 schtasks/NSSM 两条现成路径说明即可;
- 新增 `INSTALL.md`(两平台各 5 步以内),MIGRATION.md 保留作历史参考。

### R5 安全加固(可选,P2)

- 可选访问口令:`settings.access_token`,默认空 = 关闭(仅 127.0.0.1 场景);一旦监听地址改为 0.0.0.0 则强制要求设置;
- `.secret.key` 文件权限收紧(Linux chmod 600;Windows 限制到当前用户)。

---

## 三、实施分期

| 期次 | 内容 | 验收标准 |
|---|---|---|
| **一期(本次)** | R1 全部 + R2 全部 + R3 基础(徽标+版本校验) | ① 一台**没有 MySQL 的干净 Windows 主机**:解压 → install → start → 向导填远程库信息 → 备份还原可用,全程不看文档 ≤5 分钟;② Linux 同流程跑通;③ 现有 tests(test_frontend/e2e/progress)回归全绿 |
| 二期 | R4(install 脚本 + systemd)+ INSTALL.md | 两平台各一条命令完成安装;Linux `--service` 注册并开机自启 |
| 三期(可选) | R5 + 备份文件浏览器下载 | — |

**本期明确不做**:物理备份(XtraBackup)、并行多任务、监控告警、SSH 隧道。

---

## 四、风险与对策

| 风险 | 对策 |
|---|---|
| mysqldump 版本碎片化(各发行版自带路径千奇百怪) | 探测链 + 设置页手填兜底;向导第 2 步即验证 |
| 远程库防火墙/账号权限(FLUSH TABLES/LOCK TABLES) | 测试连接时同步检测 PROCESS/SELECT/LOCK TABLES 权限并在引导中提示 |
| jsdom 前端回归依赖旧机 NODE_PATH | 一并把测试脚本路径参数化(NODE_PATH 可注入) |
