# Security Policy

Thanks for helping keep **MySQL Console** safe. This document describes the security posture of the project and how to report vulnerabilities.

## Supported versions

Only the latest release receives security fixes. Updates are released as fast as practical, usually as a patch/minor version.

| Version | Supported          |
|---------|--------------------|
| Latest (3.7.x+) | ✅ Supported |
| Older releases | ❌ Please upgrade |

> The project ships a **self-update** feature (menu: *System Settings → Software Update*). Keep the app current to receive security fixes automatically. Apply it in a safe, non-production window and always keep a backup of your `data/` directory before updating.

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security problems.

- **Privately report** by opening a [Security Advisory](https://github.com/zetsubouk/mysql-console/security/advisories/new) on this repository, or
- Email the maintainer with the full reproduction details.

**What to include:**
- Affected version(s) and platform (Windows / Linux / macOS).
- A minimal reproduction: config, steps, and observed vs. expected behavior.
- Proof of concept, if safe to share.
- Any suggested fix, if you have one.

**What to expect:**
- Acknowledgment within a few days.
- A fix and (if applicable) a security release as soon as a fix is available.
- Credit in the release notes unless you prefer to stay anonymous.

## Security posture & threat model

MySQL Console is a **local admin tool**. It binds to `127.0.0.1:8090` by default and is intended to run on a machine you trust, accessed only by you. Deploying it on a public/multi-user network is not supported and should be avoided.

### What the project does well

- **Encrypted credentials**: MySQL connection passwords and sensitive settings (including the AI `api_key`) are encrypted at rest with **Fernet** (symmetric, from the `cryptography` package) in `data/`.
- **Login protection**: session authentication with **failure lockout** and password recovery.
- **Path traversal defense**: the backup download/file-browser endpoints enforce a **path whitelist**, preventing arbitrary file reads.
- **Read-only SQL by default**: the SQL query executor blocks write statements via a prefix keyword whitelist (`SELECT`/`SHOW`/`DESC`/`EXPLAIN`/`WITH`) and supports `KILL QUERY` for runaway queries.
- **Self-update safety**: updates are downloaded, checksum-verified, and the current code is backed up before the new code is applied and the process restarts. The `data/` directory and `.venv` are preserved across updates.
- **Least-privilege guidance**: the user-management UI protects the `root` account and supports granular, prefilled grant editing.

### Known considerations to be aware of

1. **Plaintext credentials in generated scheduled-backup scripts.** Native scheduler registration generates self-contained backup scripts (`backup_*.ps1` / `.sh`) that embed credentials **in plaintext**. These files are excluded from git, but you must:
   - ensure the `data/` directory (and these scripts) have restricted file permissions;
   - avoid copying or transmitting them outside the machine;
   - treat them as secrets.
2. **AI assistant sends context to a third party.** SQL generation, analysis, and summaries may send schema context (and, depending on your prompts, query text) to the configured LLM provider. When using **cloud** providers (DeepSeek, Tongyi/Alibaba, etc.), data may leave your machine. Only enable this against providers you trust, and avoid pasting sensitive data. For fully local operation, use **Ollama**.
3. **Self-update is a supply-chain point.** Updating pulls code/assets from GitHub Releases. Always confirm a release's authenticity (maintainer and tag) before applying, and prefer downloading from the official repository only.
4. **Transport:** HTTP is plaintext on loopback. It is not protected by TLS; do not expose the port to untrusted networks.

## Hardening recommendations

- Run the service on a dedicated, trusted machine; restrict who can log in at the OS level.
- Keep the OS, `data/` directory, and generated scheduler scripts under restricted permissions.
- Use a dedicated MySQL user with the least privileges needed for your tasks rather than `root`.
- If you must expose the web UI, put it behind a reverse proxy with TLS and authenticate at the proxy layer.
- Review the AI feature's provider and data before enabling it with cloud endpoints.

## Reporting responsibly

We take security seriously and will respond promptly. Thank you for coordinating disclosure rather than publishing vulnerabilities publicly before a fix is available.