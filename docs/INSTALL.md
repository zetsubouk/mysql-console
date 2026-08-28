# MySQL Console 安装部署指南

> 适用:任意 Windows / Linux / macOS 主机。
> 核心原则:**本服务不要求本机装有 MySQL**——被管理的数据库可以是本机实例,也可以是任意网络可达的独立服务器。
> 脚本位置:开发仓库中安装/启动/停止/初始化脚本位于 `scripts/`;发布包解压后这些脚本已复制到**包根目录**。两类位置均可直接双击/运行(脚本会自动定位部署根)。

---

## Windows 部署(5 步)

1. **安装 Python ≥ 3.10**(已装可跳过):https://www.python.org/downloads/windows/ ,安装时勾选 **Add python.exe to PATH**;
2. 双击 **`install.bat`**(自动建 `.venv` 并装依赖);
3. 双击 **`start.bat`** 启动服务;
4. 浏览器打开 `http://127.0.0.1:8090`;
5. 按首次向导三步完成配置(环境检测 → MySQL 客户端目录 → 数据库连接)。

停止:`stop.bat`

### 开机自启(可选)

以管理员运行一条命令注册计划任务(SYSTEM 账户,登录前即可运行;`E:\code\mysql-console` 换成你的实际目录):

```
schtasks /create /tn MySQLConsole /sc onstart /ru SYSTEM /tr "\"E:\code\mysql-console\.venv\Scripts\pythonw.exe\" \"E:\code\mysql-console\src\server.py\""
```

删除自启:`schtasks /delete /tn MySQLConsole /f`
手动立即启动:`schtasks /run /tn MySQLConsole`

> 也可用 [NSSM](https://nssm.cc/) 注册为 Windows 服务,获得更完整的服务的控制能力。

---

## Linux / macOS 部署(5 步)

```bash
# 0. 前置: Python >= 3.10 (Debian/Ubuntu: apt install python3 python3-venv)
python3 --version

# 1. 进入项目目录
cd /path/to/mysql-console

# 2. 一键安装依赖(项目内 .venv, 不污染系统环境)
./install.sh

# 3. 启动
./start.sh

# 4. 浏览器访问
#    http://127.0.0.1:8090

# 5. 完成首次向导(同 Windows 第 5 步)
```

停止:`./stop.sh`

### systemd 服务化(Linux,推荐生产使用)

```bash
sudo ./scripts/install.sh --service   # 开发仓库(发布包: 根目录 ./install.sh --service)
                                     # 注册 + 开机自启 + 立即启动
systemctl status mysql-console   # 查看状态
journalctl -u mysql-console -f   # 跟踪日志
sudo ./install.sh --remove-service   # 注销并移除服务
```

- 端口默认 8090,可用环境变量覆盖:`sudo MC_PORT=9090 ./install.sh --service`(同时需以相同方式启动);
- unit 文件由 `scripts/mysql-console.service` 模板按当前路径与用户渲染;
- 先看渲染结果不落盘:`./install.sh --print-service`;

> macOS 无 systemd:用 launchd 或 `nohup ./start.sh &`;服务器场景建议直接部署在 Linux 上。

---

## 远程数据库说明

- 向导第 3 步填写目标 MySQL 的 **host / port / 用户名密码** 即可,本机或远程均可;
- 远程库需满足:
  - MySQL 账号允许从**本服务所在主机**的 IP 连接(不是仅 `localhost`);
  - 账号具备所需权限:监控/备份需要 `SELECT`、`PROCESS`(查看进程列表)、`LOCK TABLES`;还原需要对目标库的全部权限;
- 备份/还原通过 mysqldump/mysql 客户端执行,**客户端工具必须存在于本服务所在主机**(安装包内不含,见下方 FAQ)。

## 数据与安全

| 内容 | 位置 |
|---|---|
| 连接配置(密码 Fernet 加密) | `data/config.json` |
| 加密密钥(**勿外泄**,泄露可解密配置密码) | `data/.secret.key` |
| 备份历史 / 定时任务 | `data/backup_history.json`、`data/schedule_tasks.json` |
| 默认备份目录 | `data/backups/`(可在设置中修改) |
| 运行日志 | `data/logs/operations.log` |

- 服务仅监听 `127.0.0.1`,不对局域网开放;如需远程访问 Web 界面,请自行加反向代理并配置鉴权;
- Linux systemd 部署时,建议将项目目录权限收紧到运行用户(`chmod 700 data`)。

## FAQ

**Q1:提示"未找到 mysqldump"?**
备份/还原依赖 MySQL 客户端工具(安装包不含)。三种解决任选:
① 装 MySQL Server / 仅客户端后重试(Windows 安装器勾选 Client 程序即可);② 从已有机器拷贝 `mysqldump(.exe)` 与 `mysql(.exe)` 到任意目录;③ 在「连接管理 → 服务设置」或向导第 2 步填入该目录。

**Q2:8090 端口被占用?**
临时换端口:Linux `MC_PORT=9090 ./scripts/start.sh`(支持端口参数),或改 `src/server.py` 底部 `PORT`;Windows 临时改法同(编辑 src/server.py 的 PORT)。

**Q3:Debian/Ubuntu 创建 .venv 失败?**
`apt install python3-venv` 后重新运行 install.sh。

**Q4:连接远程库报 "not allowed to connect"?**
MySQL 服务端账号未授权来源 IP,在数据库服务器上执行(GRANT 示例):
`CREATE USER 'mc'@'%' IDENTIFIED BY '***'; GRANT SELECT, PROCESS, LOCK TABLES ON *.* TO 'mc'@'%';`

**Q5:升级到新版本?**
覆盖代码目录后重启即可;`data/` 目录承载全部用户数据,升级前备份该目录。
