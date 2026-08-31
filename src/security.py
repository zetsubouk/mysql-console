# -*- coding: utf-8 -*-
"""0.0.0.0 暴露时的安全加固:访问令牌 / CSRF / TLS 自签证书(2026-08-31)。

设计要点:
- 访问令牌相当于「控制台大门钥匙」:仅当绑定到非回环地址(host != 127.0.0.1/localhost/::1)
  时强制启用,回环直连(默认)完全不受影响,保持既有体验零差异。
- 令牌来源二选一(环境变量优先):MC_ACCESS_TOKEN 或 settings.access_token(Fernet 加密落库)。
- 会话认证为 Bearer Header(非 Cookie),本身已抗经典 CSRF;此处再加 Origin/Host 一致性校验
  作为纵深防御(防御 DNS Rebind / 恶意站点诱导本地浏览器对本地 API 发起写请求)。
- TLS:MC_TLS=1 启用,自签证书由 cryptography 自动生成到 data/tls/(可用 MC_CERT/MC_KEY 覆盖)。
"""
import hashlib
import hmac
import ipaddress
import os
import ssl
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import config_store


def target_host() -> str:
    """解析将要绑定的主机地址(与 server.py 一致,读 MC_HOST)。"""
    return (os.environ.get("MC_HOST") or "127.0.0.1").strip() or "127.0.0.1"


def is_loopback(host: str = None) -> bool:
    host = host or target_host()
    if host in ("127.0.0.1", "localhost", "::1"):
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        # 无法解析的字符串(如 "0.0.0.0" 可解析;域名则保守按"需要更强防护"处理)
        return host.lower() == "localhost"
    return addr.is_loopback


def bind_port() -> int:
    try:
        return int(os.environ.get("MC_PORT") or "8090")
    except (TypeError, ValueError):
        return 8090


def access_token_required() -> bool:
    """绑定到非回环地址 → 必须提供访问令牌。"""
    return not is_loopback()


def effective_access_token() -> str:
    """生效令牌:环境变量优先于设置。"""
    env = (os.environ.get("MC_ACCESS_TOKEN") or "").strip()
    if env:
        return env
    return (config_store.get_access_token() or "").strip()


def check_access_token(incoming) -> bool:
    """常数时间比较令牌,防时序侧信道。"""
    if not incoming:
        return False
    expected = effective_access_token()
    if not expected:
        return False
    return hmac.compare_digest(expected, incoming)


# ---------------- CSRF(Origin/Host 一致性) ----------------
def _netloc_norm(h: str) -> str:
    """归一化 netloc:去掉 scheme 前缀与默认端口差异,便于比较。"""
    h = h.strip().lower()
    if "://" in h:
        h = urlparse(h).netloc
    # 去掉末尾默认端口:http:80 / https:443
    if h.endswith(":80"):
        h = h[:-3]
    elif h.endswith(":443"):
        h = h[:-4]
    return h.rstrip("/")


def origin_allowed(request_host, origin, allow_null=True) -> bool:
    """校验请求 Origin 与 Host 是否同源。

    - 无 Origin(非浏览器客户端/同源旧岐义)→ 放行(allow_null)。
    - 有 Origin:取其 host(netloc)与 request_host 归一化后比较,一致才放行。
    """
    if not origin:
        return allow_null
    if origin in ("null", "None", "undefined"):
        return allow_null
    o_host = _netloc_norm(origin)
    r_host = _netloc_norm(request_host)
    if not o_host or not r_host:
        # 无法解析时:有 Origin 却解析不出 host → 保守放行空情况,其余依比较结论
        return allow_null if not o_host else False
    return o_host == r_host


# ---------------- TLS(自签证书) ----------------
def tls_enabled() -> bool:
    return (os.environ.get("MC_TLS") or "").strip() in ("1", "true", "yes", "on")


def cert_paths(data_dir):
    """返回 (cert, key) 路径。默认 data/tls/ 下自签;可用 MC_CERT/MC_KEY 覆盖。"""
    c = (os.environ.get("MC_CERT") or "").strip()
    k = (os.environ.get("MC_KEY") or "").strip()
    if c and k:
        return c, k
    base = os.path.join(data_dir, "tls")
    cert = os.path.join(base, "server.crt")
    key = os.path.join(base, "server.key")
    return cert, key


def _gen_self_signed(cert_path, key_path, host="127.0.0.1"):
    """用 cryptography 生成自签证书(IP SAN 中含绑定主机,便于浏览器信任环回连接)。"""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, host or "mysql-console"),
    ])
    now = datetime.now(timezone.utc)
    try:
        san_ips = [ipaddress.ip_address(host)] if host else []
    except ValueError:
        san_ips = []
    san_dns = [] if host in ("127.0.0.1", "localhost", "::1", "0.0.0.0") else [host]
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ip) for ip in san_ips] + [x509.DNSName(d) for d in san_dns]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    os.makedirs(os.path.dirname(cert_path), exist_ok=True)
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))


def ensure_cert(data_dir, host=None) -> tuple:
    """确保证书存在,返回 (cert, key)。"""
    cert, key = cert_paths(data_dir)
    if not (os.path.exists(cert) and os.path.exists(key)):
        _gen_self_signed(cert, key, host or "127.0.0.1")
    return cert, key


def wrap_socket(raw_sock, data_dir, host=None):
    """用 TLS 上下文包装已绑定 socket(server_side)。"""
    cert, key = ensure_cert(data_dir, host)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    return ctx.wrap_socket(raw_sock, server_side=True)