# 发版流程（RELEASE 手册）

> 仓库状态:**公开** `zetsubouk/mysql-console`(private=false);私有库 `mysql-console-archive` 仅存档、不再发布。
> 目标:每次发版 **0 泄露** + 资产规范 + 更新记录对人友好。自 v3.3.0 起含自动更新,发版即触发旧版本用户界面的"一键升级"。

---

## 版本号(唯一权威)

- 唯一来源:`src/version.py` 的 `__version__`。
- 改版规则:功能增量升 minor(`v3.3.0`→`v3.4.0`);纯修复升 patch(`v3.3.0`→`v3.3.1`)。
- 同步 `DEVLOG.md` 时间线加一行发布记录。

## 发版五步

### ① 脱敏扫描(必做,任一命中即停,先修再发)

> 用**通用模式**扫描。具体敏感词表(生产/演示库名、自定义端口、本机用户等)见**本地技能** `mysql-console` 的 `references/release-runbook.md`,**严禁写入本文件/仓库**。

```bash
cd <项目根>
# 内网/私有 IP(白名单 127.0.0.1)
git ls-files -z | xargs -0 grep -nE '\b(192\.168|10\.|172\.(1[6-9]|2[0-9]|3[01]))\.' 2>/dev/null | grep -v echarts.min.js
# 自定义端口/服务指纹(非默认 3306;具体端口词表见本地 runbook)
git ls-files -z | xargs -0 grep -nE ':(330[0-9]|33[1-9][0-9])\b' 2>/dev/null
# 本机身份 / 绝对路径(用占位,勿写真实用户名)
git ls-files -z | xargs -0 grep -inE '\\(Users|home)\\|<本机用户名>|C:\\\\Users\\\\' 2>/dev/null
# 生产/演示库命名特征(**词表只在本机**,见本地 runbook)
git ls-files -z | xargs -0 grep -inE '<客户产品代号>demo|_sp[0-9]' 2>/dev/null
# 明文密钥样式赋值
git ls-files -z | xargs -0 grep -inE "(password|secret|token|api_key)['\"]?\s*[:=]\s*['\"][^'\"]{6,}" 2>/dev/null \
  | grep -vE 'placeholder|admin_password_hash|has_password|password_enc'
# 全历史(转公开后历史也会暴露,一劳永逸重写后再公开)
git log --all --oneline   # 确认不含上述指纹的提交
```

命中任一:先在本地清除,提交干净后再进下一步。**绝不允许带指纹发版。**

### ② 本地提交 + 推送(公开库)

```bash
python scripts/regen_manifest.py     # 文件有增删时必跑:再生 docs/MANIFEST.txt
git add -A && git commit -m "chore: 发版 vX.Y.Z(版本提升 + DEVLOG 发布行)" && git push origin main
```

### ③ 打 tag + 打包资产(自动化,替代手工 git archive)

```bash
git tag -a vX.Y.Z -m "mysql-console vX.Y.Z" && git push origin vX.Y.Z
python scripts/build_release.py --tag vX.Y.Z
# 产出(自动校验后打印 sha256):
#   dist/mysql-console-vX.Y.Z.zip / mysql-console-vX.Y.Z.tar.gz
# 校验内容由脚本自动执行:
#   - 必须含: src/server.py src/version.py src/paths.py src/static/index.html(等 static/*)、
#             包根 install/start/stop/init(.bat/.sh)、mysql-console.service、
#             README.md LICENSE requirements.txt、docs/*;
#   - 必须剔除: tests/ data/ .venv node_modules _pydeps package.json package-lock.json .github;
#   - 结构: 源码全部在 src/,与开发仓库一致(自更新/文档路径零迁移)。
# 可选:pip install . 后可用 `mysql-console` 命令启动(pip 形态数据默认 ~/.mysql-console)。
```

### ④ 创建 release + 更新记录 + 上传资产(REST)

- token 取 **git 凭据管理器**:`printf 'protocol=https\nhost=github.com\n\n' | git credential fill`(**勿打印 password 行**)。
- `POST /repos/zetsubouk/mysql-console/releases`
  body:`{tag_name, target_commitish:"main", name:"mysql-console vX.Y.Z", body:<更新记录>, draft:false, prerelease:false}`
  Header: `Authorization: Bearer <token>` + `X-GitHub-Api-Version: 2022-11-28` + `User-Agent` + `Content-Type: application/json`。
- 从返回 `upload_url` 去掉 `{?name,label}` 后缀,逐资产 `POST <upload_url>?name=<文件名>`,
  tar.gz→`application/gzip`、zip→`application/zip`。
- **更新记录模板**(脱敏):
  ```
  mysql-console vX.Y.Z
  == 更新 ==
  - <功能 1 一句人话含"为什么">
  - <修复 2>
  == 安装 ==
  Python≥3.10 + pymysql/cryptography;install.bat / install.sh[--service];start → http://127.0.0.1:8090;首启向导。
  ```

### ⑤ API 回读验证(发完必验)

```bash
GET /repos/zetsubouk/mysql-console/releases/tags/<tag>   # draft=false,每 asset state=uploaded
GET /repos/zetsubouk/mysql-console                       # private=false
git rev-parse <tag>^{commit} == git rev-parse origin/main   # tag 对齐远端
```

通过才交付。收尾可补 DEVLOG 发布记录(纯文档,不影响已发布资产)。

---

## 红线(违反必翻车)

1. **公开库历史会暴露**:已压缩为单干净提交,日后 commit 直接 push 公开库,严禁写入任何敏感指纹。
2. **发版前必跑 ① 脱敏扫描**;生产/演示库名与自定义端口等具体词表只在本地技能 runbook,不进仓库。
3. git archive 只含 git 已跟踪文件,`data/`、`.venv/`、`__pycache__/` 天然排除;但**敏感文件一旦先进过历史**,须重写历史才清除——先查再推。
4. token 经 git 凭据管理器,勿打印、勿进代码/日志。