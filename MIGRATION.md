# 迁移部署指南

> 本包为 MySQL Console 完整项目,可在新开发环境直接运行。

## 1. 环境要求

| 组件 | 要求 | 说明 |
|---|---|---|
| Python | ≥ 3.10(开发环境为 3.13) | 需加入 PATH 或修改 bat 中的路径 |
| PyMySQL | 2.2.8 | `pip install pymysql` |
| cryptography | 50.0.0 | `pip install cryptography`(Fernet 加密用) |
| Node + jsdom | 可选 | 仅跑前端回归测试 `tests/test_frontend.js` 用 |
| MySQL | 5.7 / 8.x | 备份还原调用 mysqldump.exe / mysql.exe |

一键安装依赖:
```bash
pip install -r requirements.txt
```

## 2. 路径适配(迁移必查)

以下硬编码路径需按新环境调整:

| 位置 | 内容 | 改法 |
|---|---|---|
| `start.bat` 第 19 行 | python 解释器绝对路径 | 改为新环境 python 路径,或改为 `python server.py` |
| `README.md` / `server.py` 中 mysqldump 路径配置 | 自动探测 PATH/常见安装目录中的 `mysqldump`/`mysql` | 在网页「备份与还原」设置中修改,或改 `data/config.json` |
| `cli_backup.py` / `native_scheduler.py` 生成的计划任务命令 | python 绝对路径 | 重新保存定时任务即可自动按新路径注册 |
| 默认端口 | 8090 | 如冲突可改 `server.py` 底部监听端口 |

## 3. data 目录说明

| 文件 | 是否随包携带 | 说明 |
|---|---|---|
| `config.json` | 是 | 连接配置,**密码已加密**;换机器后建议重新录入连接 |
| `.secret.key` | 是 | Fernet 密钥;保留则旧密码可解密,**勿外泄** |
| `backup_history.json` / `schedule_tasks.json` | 是 | 历史/任务数据,可直接沿用 |
| `backups/`、`logs/` | 空目录 | 运行时自动写入 |

> 安全提示:若在新环境视为不可信来源,可删除 `data/.secret.key`,首次启动后重新配置连接。

## 4. 启动

```bash
# Windows
start.bat          # 启动(自动清理占用 8090 的旧实例)
stop.bat           # 停止(杀掉监听 8090 的进程并二次校验)

# 任意平台
python server.py
```

浏览器访问 http://127.0.0.1:8090 ,在「连接管理」录入 MySQL 凭据并激活。

## 5. 测试验证

```bash
python tests/test_e2e.py         # 备份→还原端到端(自动建测试库并清理)
python tests/test_progress.py    # 异步进度(测试库 8 万行)
node tests/test_frontend.js      # 前端回归(需 jsdom)
```

## 6. 更多文档

- `README.md`:功能总览与技术要点
- `DEVLOG.md`:开发记录、10 轮 bug 修复经验、后续优化方向
