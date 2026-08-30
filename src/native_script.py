# -*- coding: utf-8 -*-
"""系统计划任务备份脚本生成器(2026-08-30 新增)。

任务注册到系统计划任务(schtasks / systemd timer / crontab)时,生成一份
**自包含备份脚本**,计划任务只调用脚本,不再经过 python 解释器:

  Windows -> scripts\\backup_<id>.ps1   (powershell.exe -File)
  Linux   -> scripts/backup_<id>.sh    (/bin/bash)

脚本逻辑对齐本机成熟的 PowerShell 备份脚本方案(mysqldump + 校验 + 压缩 + 清理):
  逐库 mysqldump(--opt --single-transaction=TRUE --quick --triggers -R,
  MYSQL_PWD 环境变量传密码不落命令行, --result-file 直写文件规避编码问题)
  -> 大小>=1KB 校验 -> 头部 "MySQL dump" 校验 -> gzip 压缩(.sql.gz)
  -> 删源文件 -> 按 keep 保留最近 N 份 -> UTF-8 追加日志 -> 退出码=失败库数。

产物命名与内置备份一致 {db}_{YYYYmmdd_HHMMSS}.sql.gz,可被 Web 还原识别。
凭据以明文内嵌(与本机 PowerShell 备份方案同等);Linux 脚本生成后 chmod 700 仅属主可读。
本模块为纯标准库,零第三方依赖。
"""
import os
import time

MIN_SIZE = 1024
CHARSET = "utf8mb4"

_SYS_DBS = ("information_schema", "performance_schema", "mysql", "sys")


def _ps1_squote(s):
    """PowerShell 单引号字符串转义: 内部单引号翻倍。"""
    return "'" + str(s).replace("'", "''") + "'"


def _sh_squote(s):
    """bash 单引号字符串转义: 内部单引号以 '\'' 闭合再开。"""
    return "'" + str(s).replace("'", "'\\''") + "'"


def _default_backup_dir():
    """与 backup_engine.DEFAULT_BACKUP_DIR 对齐: <data>/backups。"""
    import paths
    return os.path.join(paths.DATA_DIR, "backups")


def _render(template, mapping):
    for k, v in mapping.items():
        template = template.replace("__" + k + "__", str(v))
    return template


def _render_dbs_ps1(dbs):
    return ",".join(_ps1_squote(d) for d in dbs) or ""


def _render_dbs_sh(dbs):
    return " ".join(_sh_squote(d) for d in dbs)


def _gen_time():
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ---------------- Windows PowerShell ----------------

_PS1_TEMPLATE = r"""# ============================================================
# MySQL Console 定时备份脚本(自动生成,请勿手改)
# 任务: __TASK_NAME__     id: __TASK_ID__
# 生成时间: __GEN_TIME__
# 修改任务后重新注册即可重新生成本脚本
# ============================================================
$ErrorActionPreference = "Continue"

# ---- Config ----
$MYSQL_BIN  = "__MYSQL_BIN__"      # 可为空,自动从 PATH 探测
$BACKUP_DIR = "__BACKUP_DIR__"
$DB_HOST    = "__HOST__"
$PORT       = "__PORT__"
$USER       = "__USER__"
$PASS       = __PASS__
$CHARSET    = "utf8mb4"
$KEEP       = __KEEP__
$MIN_SIZE   = 1024
$LOG_FILE   = Join-Path $BACKUP_DIR "mysqlconsole_backup___TASK_ID__.log"
$DBS        = @(__DBS__)
$ALL_MODE   = __ALL_MODE__

function Write-Log($msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::AppendAllText($LOG_FILE, $line + "`r`n", $utf8NoBom)
    Write-Host $line
}

Add-Type -AssemblyName System.IO.Compression | Out-Null

# ---- 探测 MySQL 客户端目录(mysqldump / mysql 同目录) ----
$BIN_DIR = ""
if ($MYSQL_BIN -and (Test-Path (Join-Path $MYSQL_BIN "mysqldump.exe"))) {
    $BIN_DIR = $MYSQL_BIN
} else {
    $cmd = Get-Command mysqldump -ErrorAction SilentlyContinue
    if ($cmd) { $BIN_DIR = Split-Path $cmd.Source }
}
if (-not $BIN_DIR) {
    Write-Log "ERROR: 未找到 mysqldump,请在「连接管理 → 设置」中配置 MySQL 客户端目录"
    exit 1
}
$MYDUMP = Join-Path $BIN_DIR "mysqldump.exe"
$MYSQL  = Join-Path $BIN_DIR "mysql.exe"

if (-not (Test-Path $BACKUP_DIR)) { New-Item -ItemType Directory -Path $BACKUP_DIR -Force | Out-Null }

# ---- 全库模式:动态枚举用户库(排除系统库) ----
if ($ALL_MODE) {
    $env:MYSQL_PWD = $PASS
    $listArgs = @("--host=$DB_HOST", "--port=$PORT", "--user=$USER", "--protocol=tcp",
                  "--default-character-set=$CHARSET", "--batch", "--skip-column-names", "-e",
                  "SELECT schema_name FROM information_schema.schemata WHERE schema_name NOT IN ('information_schema','performance_schema','mysql','sys') ORDER BY schema_name")
    $rows = & $MYSQL @listArgs 2>$null
    $env:MYSQL_PWD = $null
    if ($LASTEXITCODE -ne 0 -or -not $rows) {
        Write-Log "ERROR: 枚举数据库失败"
        exit 1
    }
    $DBS = @($rows | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

$failCount = 0
$TS = Get-Date -Format "yyyyMMdd_HHmmss"
Write-Log "============ MySQL Console backup START ($($DBS.Count) dbs) ============"

foreach ($db in $DBS) {
    Write-Log "---- [$db] start ----"
    $SQL_FILE = Join-Path $BACKUP_DIR ("{0}_{1}.sql" -f $db, $TS)
    $GZ_FILE  = Join-Path $BACKUP_DIR ("{0}_{1}.sql.gz" -f $db, $TS)

    # 1. 端口检查(仅告警,不阻断)
    if (-not (Get-NetTCPConnection -LocalPort $PORT -State Listen -ErrorAction SilentlyContinue)) {
        Write-Log "WARN: MySQL 端口 $PORT 未监听"
    }

    # 2. mysqldump: --result-file 直写文件,规避 PowerShell 重定向编码问题
    #    经 cmd 执行使 stderr 保持明文文本(参考 backup_mysql.ps1 做法)
    $env:MYSQL_PWD = $PASS
    $dumpErr = "$SQL_FILE.err"
    $dumpArgs = @("--opt", "--single-transaction=TRUE", "--quick", "--triggers", "-R",
                  "--user=$USER", "--host=$DB_HOST", "--protocol=tcp", "--port=$PORT",
                  "--default-character-set=$CHARSET", "--result-file=$SQL_FILE", $db)
    $dumpArgsStr = ($dumpArgs | ForEach-Object { "`"$_`"" }) -join " "
    $dumpCmd = "`"$MYDUMP`" $dumpArgsStr 2>`"$dumpErr`""
    cmd /c $dumpCmd | Out-Null
    $env:MYSQL_PWD = $null
    $errText = ""
    if (Test-Path $dumpErr) {
        $errText = Get-Content $dumpErr -Raw -ErrorAction SilentlyContinue
        Remove-Item $dumpErr -Force -ErrorAction SilentlyContinue
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Log "ERROR: [$db] mysqldump 失败: $errText"
        if (Test-Path $SQL_FILE) { Remove-Item $SQL_FILE -Force }
        $failCount++
        continue
    }

    # 3. 大小校验
    $size = (Get-Item $SQL_FILE).Length
    if ($size -lt $MIN_SIZE) {
        Write-Log "ERROR: [$db] 备份过小($size B),删除"
        Remove-Item $SQL_FILE -Force
        $failCount++
        continue
    }

    # 4. 头部校验(前 20 行含 "MySQL dump" 标记)
    $isValid = $false
    try {
        $sr = New-Object System.IO.StreamReader($SQL_FILE)
        for ($i = 0; $i -lt 20 -and -not $sr.EndOfStream; $i++) {
            if ($sr.ReadLine() -match "MySQL dump") { $isValid = $true; break }
        }
        $sr.Close()
    } catch { }
    if (-not $isValid) { Write-Log "WARN: [$db] 不是有效的 mysqldump 文件" }

    # 5. gzip 压缩(纯 gzip 单文件,与内置备份/还原兼容)
    try {
        $in  = [System.IO.File]::OpenRead($SQL_FILE)
        $out = [System.IO.File]::Create($GZ_FILE)
        $gz  = New-Object System.IO.Compression.GzipStream($out, [System.IO.Compression.CompressionLevel]::Optimal)
        $in.CopyTo($gz)
        $gz.Close(); $out.Close(); $in.Close()
        Remove-Item $SQL_FILE -Force
        Write-Log "OK: [$db] -> $GZ_FILE"
    } catch {
        Write-Log "ERROR: [$db] gzip 压缩失败: $_"
        if (Test-Path $SQL_FILE) { Remove-Item $SQL_FILE -Force }
        if (Test-Path $GZ_FILE)  { Remove-Item $GZ_FILE -Force }
        $failCount++
        continue
    }

    # 6. 清理:保留最近 KEEP 份
    $old = Get-ChildItem -Path $BACKUP_DIR -Filter ("{0}_*.sql.gz" -f $db) -ErrorAction SilentlyContinue |
           Sort-Object LastWriteTime, Name -Descending | Select-Object -Skip $KEEP
    foreach ($f in $old) {
        Remove-Item $f.FullName -Force -ErrorAction SilentlyContinue
        Write-Log "Cleanup: 删除旧备份 $($f.Name)"
    }
    Write-Log "---- [$db] end ----"
}

Write-Log "============ END (fail: $failCount) ============"
exit $failCount
"""


def _ps1_mapping(task, conn_cfg, settings, backup_dir, all_mode):
    return {
        "TASK_NAME": (task.get("name") or "").replace("`", "`'"),
        "TASK_ID": task["id"],
        "GEN_TIME": _gen_time(),
        "MYSQL_BIN": (settings.get("mysql_bin") or "").strip().strip('"'),
        "BACKUP_DIR": backup_dir.replace("\\", "/"),
        "HOST": conn_cfg.get("host", "127.0.0.1"),
        "PORT": int(conn_cfg.get("port", 3306)),
        "USER": conn_cfg.get("user", "root"),
        "PASS": _ps1_squote(conn_cfg.get("password", "")),
        "KEEP": int(task.get("keep", 7)),
        "DBS": _render_dbs_ps1(task.get("dbs") or []),
        "ALL_MODE": "1" if all_mode else "0",
    }


def _write_ps1(content, path):
    # UTF-8 BOM:PowerShell 5.1 读取无 BOM 的 UTF-8 会把中文当 ANSI 导致乱码
    with open(path, "wb") as f:
        f.write(b"\xef\xbb\xbf")
        f.write(content.encode("utf-8").replace(b"\n", b"\r\n"))


# ---------------- Linux bash ----------------

_SH_TEMPLATE = r"""#!/usr/bin/env bash
# ============================================================
# MySQL Console 定时备份脚本(自动生成,请勿手改)
# 任务: __TASK_NAME__     id: __TASK_ID__
# 生成时间: __GEN_TIME__
# 修改任务后重新注册即可重新生成本脚本
# ============================================================
set -u

# ---- Config ----
MYSQL_BIN="__MYSQL_BIN__"          # 可为空,自动从 PATH 探测
BACKUP_DIR="__BACKUP_DIR__"
DB_HOST="__HOST__"
PORT="__PORT__"
USER="__USER__"
PASS=__PASS__
CHARSET="utf8mb4"
KEEP=__KEEP__
MIN_SIZE=1024
LOG_FILE="$BACKUP_DIR/mysqlconsole_backup___TASK_ID__.log"
DBS=(__DBS__)
ALL_MODE=__ALL_MODE__

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

# ---- 探测 MySQL 客户端目录(mysqldump / mysql 同目录) ----
BIN_DIR=""
if [ -n "$MYSQL_BIN" ] && [ -x "$MYSQL_BIN/mysqldump" ]; then
  BIN_DIR="$MYSQL_BIN"
else
  D=$(command -v mysqldump 2>/dev/null) && BIN_DIR="$(dirname "$D")"
fi
if [ -z "$BIN_DIR" ]; then
  log "ERROR: 未找到 mysqldump,请在「连接管理 → 设置」中配置 MySQL 客户端目录"
  exit 1
fi
MYDUMP="$BIN_DIR/mysqldump"
MYSQL="$BIN_DIR/mysql"

mkdir -p "$BACKUP_DIR"

# ---- 全库模式:动态枚举用户库(排除系统库) ----
if [ "$ALL_MODE" = "1" ]; then
  DBS=()
  while IFS= read -r line; do
    [ -n "$line" ] && DBS+=("$line")
  done < <(MYSQL_PWD="$PASS" "$MYSQL" --host="$DB_HOST" --port="$PORT" --user="$USER" --protocol=tcp \
      --default-character-set="$CHARSET" --batch --skip-column-names -e \
      "SELECT schema_name FROM information_schema.schemata WHERE schema_name NOT IN ('information_schema','performance_schema','mysql','sys') ORDER BY schema_name" 2>/dev/null)
  if [ "${#DBS[@]}" -eq 0 ]; then
    log "ERROR: 枚举数据库失败或为空"
    exit 1
  fi
fi

failCount=0
TS=$(date '+%Y%m%d_%H%M%S')
log "============ MySQL Console backup START (${#DBS[@]} dbs) ============"

for db in "${DBS[@]}"; do
  log "---- [$db] start ----"
  SQL_FILE="$BACKUP_DIR/${db}_${TS}.sql"
  GZ_FILE="$BACKUP_DIR/${db}_${TS}.sql.gz"
  ERR_FILE="$BACKUP_DIR/.${db}_${TS}.err"

  # 1. 端口检查(仅告警,不阻断)
  if ! (exec 3<>"/dev/tcp/$DB_HOST/$PORT") 2>/dev/null; then
    log "WARN: MySQL 端口 $PORT 未监听"
  fi

  # 2. mysqldump: --result-file 直写文件,规避 shell 重定向编码问题
  if MYSQL_PWD="$PASS" "$MYDUMP" --opt --single-transaction=TRUE --quick --triggers -R \
       --user="$USER" --host="$DB_HOST" --protocol=tcp --port="$PORT" \
       --default-character-set="$CHARSET" --result-file="$SQL_FILE" "$db" 2>"$ERR_FILE"; then
    :
  else
    log "ERROR: [$db] mysqldump 失败: $(head -c 500 "$ERR_FILE" 2>/dev/null)"
    rm -f "$SQL_FILE" "$ERR_FILE"
    failCount=$((failCount+1))
    continue
  fi
  rm -f "$ERR_FILE"

  # 3. 大小校验
  size=$(stat -c%s "$SQL_FILE" 2>/dev/null || echo 0)
  if [ "$size" -lt "$MIN_SIZE" ]; then
    log "ERROR: [$db] 备份过小(${size} B),删除"
    rm -f "$SQL_FILE"
    failCount=$((failCount+1))
    continue
  fi

  # 4. 头部校验(前 20 行含 "MySQL dump" 标记)
  if ! head -20 "$SQL_FILE" | grep -q "MySQL dump"; then
    log "WARN: [$db] 不是有效的 mysqldump 文件"
  fi

  # 5. gzip 压缩(纯 gzip 单文件,与内置备份/还原兼容)
  if gzip -9 -f "$SQL_FILE"; then
    log "OK: [$db] -> $GZ_FILE"
  else
    log "ERROR: [$db] gzip 压缩失败"
    rm -f "$SQL_FILE"
    failCount=$((failCount+1))
    continue
  fi

  # 6. 清理:保留最近 KEEP 份(文件名按时间戳字典序即时间序)
  ls -1 "$BACKUP_DIR"/"${db}"_*.sql.gz 2>/dev/null | sort -r | tail -n +$((KEEP+1)) | while read -r f; do
    rm -f "$f"
    log "Cleanup: 删除旧备份 $(basename "$f")"
  done
  log "---- [$db] end ----"
done

log "============ END (fail: $failCount) ============"
exit $failCount
"""


def _sh_mapping(task, conn_cfg, settings, backup_dir, all_mode):
    return {
        "TASK_NAME": (task.get("name") or "").replace("`", "`'"),
        "TASK_ID": task["id"],
        "GEN_TIME": _gen_time(),
        "MYSQL_BIN": (settings.get("mysql_bin") or "").strip().strip('"'),
        "BACKUP_DIR": backup_dir,
        "HOST": conn_cfg.get("host", "127.0.0.1"),
        "PORT": int(conn_cfg.get("port", 3306)),
        "USER": conn_cfg.get("user", "root"),
        "PASS": _sh_squote(conn_cfg.get("password", "")),
        "KEEP": int(task.get("keep", 7)),
        "DBS": _render_dbs_sh(task.get("dbs") or []),
        "ALL_MODE": "1" if all_mode else "0",
    }


def _write_sh(content, path):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    try:
        os.chmod(path, 0o700)   # 含明文密码:仅属主可读写执行
    except OSError:
        pass


# ---------------- 对外入口 ----------------

def build(task, conn_cfg, settings, script_dir, os_type):
    """生成当前平台备份脚本,返回脚本绝对路径。

    task/conn_cfg/settings 与 cli_backup.py 同源;os_type 取 "windows" / "linux"。
    """
    backup_dir = (task.get("backup_dir") or settings.get("backup_dir") or "").strip() \
        or _default_backup_dir()
    all_mode = not bool(task.get("dbs"))
    os.makedirs(script_dir, exist_ok=True)

    if os_type == "windows":
        path = os.path.join(script_dir, "backup_%s.ps1" % task["id"])
        _write_ps1(_render(_PS1_TEMPLATE, _ps1_mapping(task, conn_cfg, settings, backup_dir, all_mode)), path)
        return path
    path = os.path.join(script_dir, "backup_%s.sh" % task["id"])
    _write_sh(_render(_SH_TEMPLATE, _sh_mapping(task, conn_cfg, settings, backup_dir, all_mode)), path)
    return path
