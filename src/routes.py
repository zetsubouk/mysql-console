# -*- coding: utf-8 -*-
"""声明式路由注册表 + 分发器(2026-08-31 从 server.py 巨型 if-elif 链拆出)。

作用:
- 把 Handler 里散落的 "if path == '/api/xxx': ..." 收敛为一张数据表,统一精确/前缀匹配与分发;
- 路由值 = (匹配器, 目标方法名, 参数形态)。argkind ∈:
    none      → fn()
    body      → fn(body)
    path      → fn(path)
- 目标方法仍是 Handler 的方法(可用 self 访问 server.py 上下文),因此移动的是“路由索引/分发”
  而非处理逻辑——这是最低风险、可被现有测试充分覆盖的拆分方式。
"""
from __future__ import annotations


def _exact(spec):   return ("exact", spec)
def _prefix(spec):  return ("prefix", spec)


# ---------------- GET 路由 ----------------
GET_ROUTES = [
    # (匹配器, 方法名, argkind)
    (_exact("/api/health"),                   "g_health",                "none"),
    (_exact("/api/auth-status"),              "g_auth_status",           "none"),
    (_exact("/api/security/info"),            "g_security_info",         "none"),
    (_exact("/api/connections"),              "g_connections",           "none"),
    (_exact("/api/overview"),                 "g_overview",              "none"),
    (_exact("/api/databases"),                "g_databases",             "none"),
    (_exact("/api/users"),                    "g_users",                 "none"),
    (_exact("/api/processlist"),              "g_processlist",           "none"),
    (_exact("/api/monitor"),                  "g_monitor",               "none"),
    (_exact("/api/monitor/full"),             "g_monitor_full",          "none"),
    (_exact("/api/sys-resource"),             "g_sys_resource",          "none"),
    (_exact("/api/dashboard/health"),         "g_dashboard_health",      "none"),
    (_exact("/api/dashboard/innodb"),         "g_dashboard_innodb",      "none"),
    (_exact("/api/dashboard/tablespace"),     "g_dashboard_tablespace",  "none"),
    (_exact("/api/dashboard/health-history"), "g_dashboard_health_history", "path"),
    (_exact("/api/dashboard/replication"),    "g_dashboard_replication", "none"),
    (_exact("/api/alerts"),                   "g_alerts",                "none"),
    (_exact("/api/alerts/history"),          "g_alerts_history",        "path"),
    (_exact("/api/variables"),                "g_variables",             "none"),
    (_exact("/api/service/status"),           "g_service_status",        "none"),
    (_exact("/api/backups"),                  "g_backups",               "none"),
    (_exact("/api/backup-params"),            "g_backup_params",         "none"),
    (_exact("/api/backup-files"),             "g_backup_files",          "none"),
    (_exact("/api/backup-files/download"),    "g_backup_download",       "none"),
    (_exact("/api/logs"),                     "g_logs",                  "none"),
    (_exact("/api/settings"),                 "g_settings",              "none"),
    (_exact("/api/setup/env"),                "g_setup_env",             "none"),
    (_exact("/api/schedules"),                "g_schedules",             "none"),
    (_exact("/api/schedules/env"),            "g_schedules_env",         "none"),
    (_exact("/api/version"),                  "g_version",               "none"),
    (_exact("/api/update/check"),             "g_update_check",          "none"),
    (_exact("/api/update/badge"),             "g_update_badge",          "none"),
    (_exact("/api/update/status"),            "g_update_status",         "none"),
    (_exact("/api/ai/config"),                "g_ai_config",             "none"),
    # 前缀
    (_prefix("/api/users/"),                  "g_user_detail_or_grants", "path"),
    (_prefix("/api/task/"),                   "g_task",                  "path"),
    (_prefix("/api/schedules/"),              "g_schedule_detail",       "path"),
    (_prefix("/api/databases/"),              "g_database_detail",       "path"),
]


# ---------------- POST 路由 ----------------
POST_ROUTES = [
    (_exact("/api/login"),                    "_handle_login",           "body"),
    (_exact("/api/logout"),                   "_handle_logout",          "none"),
    (_exact("/api/change-password"),          "_handle_change_password", "body"),
    (_exact("/api/change-username"),          "_handle_change_username", "body"),
    (_exact("/api/switch-to-full-mode"),      "_handle_switch_to_full_mode", "body"),
    (_exact("/api/request-reset-code"),       "_handle_request_reset_code", "none"),
    (_exact("/api/reset-password"),           "_handle_reset_password",  "body"),
    (_exact("/api/connections"),              "p_connections",           "body"),
    (_exact("/api/connections/test"),         "p_connections_test",      "body"),
    (_exact("/api/connections/remote-check"), "p_connections_remote_check", "body"),
    (_exact("/api/setup/probe-client"),       "p_setup_probe_client",    "body"),
    (_exact("/api/setup/test-db"),            "p_setup_test_db",         "body"),
    (_exact("/api/setup/db-check"),           "p_setup_db_check",        "body"),
    (_exact("/api/setup/drop-db"),            "p_setup_drop_db",         "body"),
    (_exact("/api/setup/finish"),             "p_setup_finish",          "body"),
    (_exact("/api/setup/download-tools"),     "_handle_setup_download_tools", "body"),
    (_exact("/api/connect"),                  "p_connect",               "body"),
    (_exact("/api/kill"),                     "p_kill",                  "body"),
    (_exact("/api/query"),                    "_handle_query",           "body"),
    (_exact("/api/query/kill"),               "_handle_query_kill",      "body"),
    (_exact("/api/service/restart"),          "_handle_service_restart", "none"),
    (_exact("/api/users"),                    "_handle_user_create",     "body"),
    (_exact("/api/backup"),                   "p_backup",                "body"),
    (_exact("/api/restore"),                  "p_restore",               "body"),
    (_exact("/api/backup-files/remote"),      "p_backup_files_remote",   "body"),
    (_exact("/api/dialog"),                   "p_dialog",                "body"),
    (_exact("/api/browse"),                   "p_browse",                "body"),
    (_exact("/api/schedule"),                 "p_schedule",              "body"),
    (_exact("/api/schedules"),                "p_schedules",             "body"),
    (_exact("/api/schedules/toggle"),         "p_schedules_toggle",      "body"),
    (_exact("/api/schedules/register"),       "p_schedules_register",    "body"),
    (_exact("/api/schedules/unregister"),     "p_schedules_unregister",  "body"),
    (_exact("/api/update/prepare"),           "p_update_prepare",        "body"),
    (_exact("/api/update/apply"),             "p_update_apply",          "body"),
    (_exact("/api/ai/config"),                "_handle_ai_config",       "body"),
    (_exact("/api/ai/sql-gen"),               "_handle_ai_sql_gen",      "body"),
    (_exact("/api/ai/sql-analyze"),           "_handle_ai_sql_analyze",  "body"),
    (_exact("/api/ai/report"),                "_handle_ai_report",       "body"),
    (_exact("/api/ai/test"),                 "_handle_ai_test",         "body"),
]


def _invoke(self, fn_spec, argkind, path, body):
    fn = getattr(self, fn_spec, None)
    if fn is None:
        return False
    if argkind == "none":
        fn()
    elif argkind == "body":
        fn(body)
    else:  # path
        fn(path)
    return True


# 预建索引:GET/POST 分开(同一路径既可 GET 又可 POST,如 /api/connections、/api/users)。
_GET_EXACT = {}
_GET_PREFIX = []
_POST_EXACT = {}
for _matcher, _fn, _arg in GET_ROUTES:
    _kind, _spec = _matcher
    if _kind == "exact":
        _GET_EXACT[_spec] = (_fn, _arg)
    else:
        _GET_PREFIX.append((_spec, _fn, _arg))
for _matcher, _fn, _arg in POST_ROUTES:
    _kind, _spec = _matcher
    if _kind == "exact":
        _POST_EXACT[_spec] = (_fn, _arg)
_GET_PREFIX.sort(key=lambda x: len(x[0]), reverse=True)


def dispatch(self, method, path, body=None):
    """分发请求。命中返回 True;未命中返回 False(由调用方发 404)。"""
    if method == "POST":
        exact = _POST_EXACT.get(path)
        if exact is not None:
            return _invoke(self, exact[0], exact[1], path, body)
        return False
    exact = _GET_EXACT.get(path)
    if exact is not None:
        return _invoke(self, exact[0], exact[1], path, body)
    for spec, fn, arg in _GET_PREFIX:
        if path.startswith(spec):
            return _invoke(self, fn, "path", path, body)
    return False