# -*- coding: utf-8 -*-
"""AI 客户端:OpenAI 兼容 /v1/chat/completions 调用(零第三方依赖)。

- 兼容 DeepSeek / 通义千问 / QwQ / 本地 Ollama 等 OpenAI 兼容端点;
- 配置(base_url / api_key / model)经 config_store 加解密存储,不落明文;
- 所有调用超时可控,网络/配置异常统一转 AiError,由 server 层转 400 可读错误,
  前端据此优雅降级。
"""
import json
import urllib.request
import urllib.error

# 默认端点(与 OpenAI 官方一致;可在设置中覆盖)
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
AI_TIMEOUT = 60  # 单次对话超时(秒)


class AiError(Exception):
    pass


# ---------------- 配置存取(加解密) ----------------
def _cfg():
    import config_store
    s = config_store.get_settings()
    return {
        "base_url": (s.get("ai_base_url") or "").strip() or DEFAULT_BASE_URL,
        "api_key": config_store.decrypt(s.get("ai_api_key_enc") or ""),
        "model": (s.get("ai_model") or "").strip() or DEFAULT_MODEL,
        "enabled": bool(s.get("ai_enabled")),
    }


def is_configured():
    """是否已启用且具备调用所需参数(端点可留默认,key 与 model 必须)。"""
    c = _cfg()
    return c["enabled"] and bool(c["api_key"]) and bool(c["model"])


def save_config(base_url="", api_key="", model="", enabled=False):
    """保存 AI 配置。api_key 加密落库;返回脱敏后的当前配置。"""
    import config_store
    patch = {
        "ai_base_url": (base_url or "").strip(),
        "ai_api_key_enc": config_store.encrypt(api_key or ""),
        "ai_model": (model or "").strip(),
        "ai_enabled": bool(enabled),
    }
    s = config_store.save_settings(patch)
    return {
        "base_url": (s.get("ai_base_url") or "").strip() or DEFAULT_BASE_URL,
        "model": (s.get("ai_model") or "").strip() or DEFAULT_MODEL,
        "enabled": bool(s.get("ai_enabled")),
        "has_key": bool(config_store.decrypt(s.get("ai_api_key_enc") or "")),
    }


def public_config():
    """脱敏配置(不含明文 key),供前端回填。"""
    c = _cfg()
    return {
        "base_url": c["base_url"],
        "model": c["model"],
        "enabled": c["enabled"],
        "has_key": bool(c["api_key"]),
    }


# ---------------- LLM 调用 ----------------
def _chat(messages, temperature=0.2, max_tokens=None):
    """调用 OpenAI 兼容端点,返回回复文本。messages 为 [{'role','content'},...]。"""
    c = _cfg()
    if not c["enabled"]:
        raise AiError("AI 功能未启用,请在「系统设置 → AI 设置」中启用并配置")
    if not c["api_key"]:
        raise AiError("未配置 API Key,请在「系统设置 → AI 设置」中填写")
    if not c["model"]:
        raise AiError("未配置模型名称,请在「系统设置 → AI 设置」中填写")
    base = c["base_url"].rstrip("/")
    url = base + "/chat/completions"
    payload = {
        "model": c["model"],
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + c["api_key"],
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=AI_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8")[:300]
        except Exception:
            pass
        raise AiError(f"AI 服务返回错误 {e.code}: {detail or e.reason}")
    except urllib.error.URLError as e:
        raise AiError(f"无法连接 AI 服务: {e.reason}")
    except Exception as e:
        raise AiError(f"AI 调用失败: {e}")
    try:
        return data["choices"][0]["message"]["content"]
    except Exception:
        raise AiError("AI 返回格式异常(缺少 choices)")


def _strip_code(text):
    """去掉 LLM 常带的 ```sql ... ``` 代码围栏,保留正文。"""
    t = (text or "").strip()
    if t.startswith("```"):
        lines = t.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    return t


# ---------------- 场景模板 ----------------
def generate_sql(natural_lang, schema_text):
    """自然语言 -> 只读 SQL。schema_text 为受限 schema 上下文。"""
    sys_prompt = (
        "你是资深 MySQL DBA。用户会给出自然语言描述和当前数据库的表结构上下文。\n"
        "请只输出一条可执行的只读 SQL(SELECT/SHOW/DESC/EXPLAIN/WITH 开头),"
        "不要任何解释、不要注释、不要 markdown 围栏。字段名/表名必须严格使用给出的上下文,"
        "不得臆造不存在的列。若信息不足无法生成,只输出: ERROR: <原因>"
    )
    user_prompt = f"数据库表结构(节选):\n{schema_text}\n\n需求: {natural_lang}"
    return _strip_code(_chat([
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_prompt},
    ], temperature=0.1))


def analyze_sql(sql, explain_rows, schema_text=""):
    """EXPLAIN 结果 + 命中索引 -> 优化建议。"""
    sys_prompt = (
        "你是资深 MySQL 性能优化专家。基于用户提供的 SQL 与 EXPLAIN 结果,"
        "给出简洁的中文优化建议,重点:是否全表扫描、索引是否命中、可加什么索引、"
        "SQL 如何改写。分点输出,不要超过 300 字。"
    )
    user_prompt = (
        f"表结构上下文(节选):\n{schema_text or '(未提供)'}\n\n"
        f"SQL:\n{sql}\n\nEXPLAIN 结果:\n{explain_rows}"
    )
    return _strip_code(_chat([
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_prompt},
    ], temperature=0.3))


def summarize_report(context_text, report_type):
    """告警/健康摘要报告。context_text 为服务端组装的采样数据摘要。"""
    if report_type == "alert":
        sys_prompt = (
            "你是 MySQL 运维值班助手。根据告警历史数据生成简明中文周报:"
            "告警总量与级别分布、主要问题点、趋势判断、建议措施。分点输出,不超过 400 字。"
        )
    else:
        sys_prompt = (
            "你是 MySQL 运维值班助手。根据健康评分与关键指标采样生成简明中文健康报告:"
            "总体评分趋势、异常时段、潜在风险、改进建议。分点输出,不超过 400 字。"
        )
    return _strip_code(_chat([
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": context_text},
    ], temperature=0.4))
