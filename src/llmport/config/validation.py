"""SSRF validation for provider base_url.

Targeted blocklist: rejects base_urls pointing at cloud-metadata / link-local
addresses (the high-value SSRF targets) and at the gateway's own address
(request loop). Loopback and private ranges are ALLOWED so local LLM servers
(Ollama, vLLM, internal gateways) keep working.

This is the single chokepoint: :func:`ConfigStore.save_providers_config`
validates every provider base_url via :func:`validate_providers_config`, and
the CLI calls :func:`validate_provider_base_url` for early feedback before
saving. The daemon's ``reload()`` marks any provider whose base_url fails
validation as ``"down"`` so the router skips it (defense-in-depth for
hand-edited configs).
"""

import ipaddress
import socket
from urllib.parse import urlparse

# Hostnames cloud providers reserve for instance metadata.
_METADATA_HOSTNAMES = {
    "metadata.google.internal",
    "metadata.google.internal.",
    "metadata.azure.com",
    "metadata",
}

# Alibaba cloud metadata lives on a non-link-local address.
_METADATA_IPS = {ipaddress.ip_address("100.100.100.200")}

# Hosts treated as loopback for self-loop detection (the gateway always binds
# loopback, so a provider on the same port at any of these is a self-loop).
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def _is_blocked_ip(ip: ipaddress._BaseAddress) -> bool:
    """True for cloud-metadata / link-local IPs (169.254.0.0/16 + Alibaba)."""
    if ip in _METADATA_IPS:
        return True
    # 169.254.0.0/16 link-local = AWS / GCP / Azure metadata endpoint.
    return ip.is_link_local


def _resolve(hostname: str) -> list[str]:
    """Resolve *hostname* to IP strings. Bare IPs pass through; unresolvable -> []."""
    try:
        ipaddress.ip_address(hostname)
        return [hostname]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for info in infos:
        addr = info[4][0]
        if addr not in seen:
            seen.add(addr)
            result.append(addr)
    return result


def _is_self_loop(host: str, port: int, gw_host: str, gw_port: int) -> bool:
    """True if base_url host:port is the gateway's own loopback address."""
    if port != gw_port:
        return False
    return host.lower() in _LOOPBACK_HOSTS and gw_host.lower() in _LOOPBACK_HOSTS


def validate_provider_base_url(
    url: str, gateway_host: str = "127.0.0.1", gateway_port: int = 11434
) -> None:
    """Raise ``ValueError`` if *url* is an SSRF risk (metadata / self-loop).

    Allows loopback and private ranges so local LLM servers work. A hostname
    that cannot be resolved is allowed (the connect will fail at the HTTP
    layer); only resolved metadata/link-local IPs are blocked.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        raise ValueError(f"无法解析 base_url: {url!r}")
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"base_url 必须是 http/https 地址: {url!r}")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"base_url 缺少主机名: {url!r}")
    port = parsed.port or (443 if scheme == "https" else 80)

    if _is_self_loop(hostname, port, gateway_host, gateway_port):
        raise ValueError(
            f"base_url 指向网关自身 ({gateway_host}:{gateway_port})，会形成请求循环"
        )
    if hostname.lower() in _METADATA_HOSTNAMES:
        raise ValueError(f"不允许使用云元数据地址: {hostname}")

    for addr in _resolve(hostname):
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if _is_blocked_ip(ip):
            raise ValueError(
                f"base_url 解析到元数据/链路本地地址 {addr}，存在 SSRF 风险"
            )


def validate_providers_config(cfg: dict, gateway: dict | None = None) -> None:
    """Validate every provider base_url in *cfg*. Raises ``ValueError``.

    *gateway* (``{"host", "port"}``) supplies the gateway address for the
    self-loop check; it defaults to ``127.0.0.1:11434``. Gateway lives in
    ``config.yaml`` now, not alongside providers, so callers pass it in -- the
    store's ``save_providers_config`` does this automatically.

    No-op for non-dict input or configs without providers.
    """
    if not isinstance(cfg, dict):
        return
    gw = gateway or {}
    gw_host = gw.get("host", "127.0.0.1")
    gw_port = int(gw.get("port", 11434))
    for p in cfg.get("providers", []) or []:
        if not isinstance(p, dict):
            continue
        base_url = p.get("base_url")
        if not base_url:
            continue
        validate_provider_base_url(base_url, gw_host, gw_port)
