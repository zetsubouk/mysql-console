# MySQL Console

**A zero-framework MySQL visual management platform** — a single Python service + browser lets you handle monitoring, backup, and user management for MySQL end to end. The managed database can be a local instance or any network-reachable standalone server — **the deployment machine does not need MySQL installed**.

![version](https://img.shields.io/badge/version-3.7.0-34d399) ![python](https://img.shields.io/badge/python-3.10%2B-22d3ee) ![deps](https://img.shields.io/badge/deps-pymysql%20%2B%20cryptography-a78bfa) ![platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-fbbf24) ![license](https://img.shields.io/badge/license-MIT-fb7185)

> 中文版见 [README.md](https://github.com/zetsubouk/mysql-console/blob/main/README.md) · For the Chinese README, see [README.md](https://github.com/zetsubouk/mysql-console/blob/main/README.md)

---

## ✨ Why MySQL Console

Similar tools are either heavy (web phpMyAdmin needs PHP + a web server) or require you to install MySQL locally. MySQL Console strips this down to the essentials:

| | |
|---|---|
| 🪶 **Zero framework** | Served by the Python standard library `http.server`; runtime dependencies are just `pymysql` + `cryptography` |
| 📦 **Installable without Python** | No Python installed? `install.bat` does three-tier resolution: bundled runtime → system Python (isolated venv, **never touches your system environment**) → auto-download of a private runtime (official source + CN mirrors); the full package even skips the download — fully offline |
| 🖥 **Runs without local MySQL** | The managed DB can be local or remote; `mysqldump`/`mysql` client tools are probed dynamically at three levels, and the wizard tells you exactly what is missing |
| 🌐 **Truly cross-platform** | Windows / Linux / macOS: one-click install scripts, systemd serviceization, native file dialogs (Win32 / osascript / zenity) all covered |
| 📊 **Progress-tracked backup & restore** | mysqldump streaming pipeline + byte-level/table-level real-time progress, streaming gzip compression — not a "spinner waiting for a result" |
| ⏰ **Dual-engine scheduled backup** | Built-in scheduler thread (registration-free) + system scheduled tasks (schtasks/systemd/cron), multi-task, retention policy |
| 🔐 **Secure out of the box** | Login authentication + failure lockout + password recovery; connection credentials encrypted and stored with Fernet |
| 🔄 **Self-updating** | Check GitHub Releases → download & verify → back up → self-update & restart, end to end |
| 🤖 **AI assistant** | SQL generation / performance analysis / report summaries for DeepSeek, Tongyi, and Ollama (OpenAI-compatible) |

## 🗺 Architecture Overview

Open **[docs/architecture.html](docs/architecture.html)** for an interactive architecture diagram (layered components, data flows, legend):

```
Browser SPA (ECharts) ──HTTP:8090──▶ server.py (ThreadingHTTPServer, 62 REST APIs)
                                        │
        ┌──────────┬──────────┬────────┼──────────┬──────────┐
   mysql_client  backup_    scheduled   config_store  service/  updater
   monitoring/   engine    backup      /system_db   env_probe  self-update
   users/process backup/   dual-engine Fernet enc.   alerts/    Releases
   dashboard    restore    schedule   storage       variables
        │           │         │         │           │
        ▼           ▼         ▼         ▼           ▼
   MySQL Server  mysqldump/  schtasks/ data/(SQLite/ OS API
   (local|remote)  mysql     systemd/  backups/logs) (dialogs etc.)
                 subprocess   cron
                  pipelines
```

## 🚀 Quick Start

> Detailed deployment (dual-platform, auto-start at boot, systemd, remote-DB config, FAQ) is in **[docs/INSTALL.md](docs/INSTALL.md)**.

### 1️⃣ Get the project

```bash
git clone https://github.com/zetsubouk/mysql-console.git
cd mysql-console
```

> No Python environment? Use the **full-win64 package** from the Releases page (bundled runtime, fully offline install), or run `install.bat` and confirm to auto-download a private runtime (~11 MB, installed only into the project directory, never touches the system).

### 2️⃣ One-click install + start

**Windows** (double-click):

```bat
scripts\install.bat    :: create .venv + install deps
scripts\start.bat      :: start the service
```

**Linux / macOS:**

```bash
./scripts/install.sh   # or ./install.sh at the release-package root
./scripts/start.sh
sudo ./scripts/install.sh --service   # Linux production recommended: systemd auto-start
```

### 3️⃣ Three-step wizard

Open `http://127.0.0.1:8090` in your browser and follow the wizard: **environment check → MySQL client directory → database connection**.
No mysqldump? The wizard will clearly tell you what is missing and how to install it.

> Factory reset: `init.bat` / `init.sh` (deletes all config, the system DB, and backups — use with care).

## 📦 Feature Overview

### Monitoring & Operations
- **Real-time monitoring**: connections / QPS / slow queries / threads, refreshed incrementally via `/api/monitor`
- **Data dashboard**: health score, InnoDB analysis, table spaces, replication status
- **Database/table management**: database & table stats, size ranking
- **User management**: MySQL user CRUD, grant editing (with current-state prefill), root protection
- **Process management**: process list, Kill connection
- **Alert center + server variables**: configurable thresholds, variable explanations

### Backup & Restore (core)
- Manual backup: full DB / multiple DBs, gzip compression, **byte-level + table-level dual-dimension real-time progress**
- Restore: auto-detects whether a backup bundle contains CREATE DATABASE statements, auto-creates the target DB
- Backup history + file browser + download endpoint (path whitelist to prevent arbitrary file reads)
- Scheduled backup: multi-task, retention policy, dual engine (built-in scheduler / system scheduled tasks)

### SQL Query (read-only)
- Multi-tab SQL editor with database selection and per-tab state
- Write statements blocked by a prefix keyword whitelist (`SELECT`/`SHOW`/`DESC`/`EXPLAIN`/`WITH`)
- Long-running queries can be killed (`KILL QUERY`)

### AI Assistant
- SQL generation (given a schema context of the first 20 tables), SQL performance analysis, alert/health report summaries
- OpenAI-compatible endpoints: DeepSeek / Tongyi / Ollama; `api_key` encrypted with Fernet

### Platform
- Login auth, failure lockout, password recovery, username change
- Dual backend storage: light mode (SQLite, zero-DB dependency) / full mode (system DB into MySQL, switchable)
- MySQL service status detection & restart, system resource (CPU/memory) monitoring
- Self-update (GitHub Releases check/download/verify/backup/restart)
- First-run three-step wizard, `MC_DATA_DIR` data-directory relocation, portable deployment
- Bundled runtime: three-tier resolution (bundled / system venv / private download), interactive confirmation when the version is insufficient, zero changes to system Python

## 🧪 Testing & Quality

```bash
npm ci && npm test                 # frontend jsdom regression (6 suites)
python tests/api/test_api.py       # API-layer regression (isolated data dir, no MySQL needed)
python tests/unit/test_units.py    # offline unit tests
python tests/e2e/test_e2e.py       # backup → restore end-to-end (needs MySQL)
```

All executed automatically by the three-stage pipeline in `.github/workflows/ci.yml`: backend matrix → frontend jsdom → E2E (MySQL 8 service container).

## 📁 Project Structure

```
mysql-console/
├── src/                  # all Python source + static/ (frontend, zero-build)
│   ├── server.py         # HTTP entry: API + static assets + scheduler + native dialogs
│   ├── backup_engine.py  # backup/restore engine (streaming pipeline + progress)
│   ├── mysql_client.py   # PyMySQL query wrapper (monitoring/db/users/processes)
│   ├── config_store.py   # Fernet-encrypted connection config + settings
│   ├── system_db.py      # dual backend storage (light SQLite / full MySQL system DB)
│   ├── schedule_store.py / native_scheduler.py   # scheduled-backup dual engine
│   ├── ai_client.py      # OpenAI-compatible LLM client (zero third-party deps)
│   ├── env_probe.py / service_manager.py / sys_resources.py
│   ├── updater.py / paths.py / ...
│   └── static/           # index.html / app.js / login.html / ECharts (local)
├── docs/                 # INSTALL / RELEASE / DEVLOG / HANDOFF / architecture.html
├── scripts/              # install/start/stop/init (.bat/.sh) + systemd template + build scripts
├── tests/                # api/ unit/ e2e/ frontend/ four test types
├── .github/workflows/    # three-stage CI
├── data/                 # runtime data (not tracked; MC_DATA_DIR can relocate)
└── runtime/              # bundled standalone runtime (not tracked; built into full package or downloaded by install.bat)
```

## 📖 Documentation

| Doc | Content |
|---|---|
| [docs/INSTALL.md](docs/INSTALL.md) | Deployment guide (dual-platform / systemd / remote DB / FAQ) |
| [docs/architecture.html](docs/architecture.html) | System architecture diagram (dark SVG, opens directly in a browser) |
| [docs/RELEASE.md](docs/RELEASE.md) | Release process |
| [docs/DEVLOG.md](docs/DEVLOG.md) | Development evolution history |
| [docs/HANDOFF.md](docs/HANDOFF.md) | AI/developer handoff guide |
| [docs/MIGRATION.md](docs/MIGRATION.md) | Version migration notes |

## 🔒 Security

Security-related notes are in **[SECURITY.md](SECURITY.md)**.

## 📄 License

MIT