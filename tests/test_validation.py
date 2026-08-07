"""Tests for the SSRF base_url blocklist (llmport.config.validation)."""

from unittest.mock import patch

import pytest

from llmport.config.validation import (
    validate_provider_base_url,
    validate_providers_config,
)


# ── allowed ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "https://api.openai.com",            # public
    "https://api.anthropic.com",         # public
    "http://192.168.1.5:11434",          # private (local LLM) -> allowed
    "http://10.0.0.5:8080",              # private
    "http://127.0.0.1:11435",            # loopback, different port (local Ollama)
    "http://localhost:11435",            # loopback hostname, different port
])
def test_allows_local_and_public_urls(url):
    """Loopback / private / public hosts are allowed (local LLM servers work)."""
    validate_provider_base_url(url, "127.0.0.1", 11434)  # no raise


# ── blocked: metadata / link-local ───────────────────────────────────────

@pytest.mark.parametrize("url", [
    "http://169.254.169.254",            # AWS/GCP/Azure metadata (link-local)
    "http://169.254.169.254/latest/meta-data/",
    "http://169.254.170.2",              # link-local range
    "http://100.100.100.200",            # Alibaba cloud metadata
])
def test_blocks_metadata_ips(url):
    with pytest.raises(ValueError, match="SSRF"):
        validate_provider_base_url(url, "127.0.0.1", 11434)


@pytest.mark.parametrize("host", [
    "metadata.google.internal",
    "metadata.azure.com",
])
def test_blocks_metadata_hostnames(host):
    with pytest.raises(ValueError, match="云元数据地址"):
        validate_provider_base_url(f"http://{host}", "127.0.0.1", 11434)


def test_blocks_hostname_resolving_to_metadata_ip():
    """A hostname that resolves to a link-local IP is blocked (DNS rebinding)."""
    with patch("llmport.config.validation.socket.getaddrinfo") as mock_gai:
        mock_gai.return_value = [(None, None, None, None, ("169.254.169.254", 0))]
        with pytest.raises(ValueError, match="SSRF"):
            validate_provider_base_url("http://rebind.evil", "127.0.0.1", 11434)


def test_unresolvable_hostname_allowed():
    """A hostname that can't resolve is allowed (connect-time will fail)."""
    import socket as _socket
    with patch("llmport.config.validation.socket.getaddrinfo") as mock_gai:
        mock_gai.side_effect = _socket.gaierror("unresolved")
        validate_provider_base_url("http://does-not-exist.invalid", "127.0.0.1", 11434)


# ── blocked: self-loop ───────────────────────────────────────────────────

def test_blocks_self_loop_same_port():
    """base_url pointing at the gateway's own port is a request loop."""
    with pytest.raises(ValueError, match="请求循环"):
        validate_provider_base_url("http://127.0.0.1:11434", "127.0.0.1", 11434)


def test_blocks_self_loop_localhost():
    with pytest.raises(ValueError, match="请求循环"):
        validate_provider_base_url("http://localhost:11434", "127.0.0.1", 11434)


def test_self_loop_only_when_port_matches():
    """Same loopback host on a different port is fine (local LLM)."""
    validate_provider_base_url("http://127.0.0.1:11435", "127.0.0.1", 11434)


# ── malformed ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "",                          # empty
    "not-a-url",                 # no scheme
    "ftp://example.com",         # non-http scheme
    "http://",                   # no host
])
def test_rejects_malformed_urls(url):
    with pytest.raises(ValueError):
        validate_provider_base_url(url, "127.0.0.1", 11434)


# ── validate_providers_config ──────────────────────────────────────────────────────

def test_validate_providers_config_passes_clean_providers():
    validate_providers_config({
        "gateway": {"host": "127.0.0.1", "port": 11434},
        "providers": [{"id": "p", "base_url": "https://api.openai.com"}],
    })  # no raise


def test_validate_providers_config_rejects_bad_provider():
    with pytest.raises(ValueError, match="SSRF"):
        validate_providers_config({
            "gateway": {"host": "127.0.0.1", "port": 11434},
            "providers": [
                {"id": "p", "base_url": "http://169.254.169.254"},
            ],
        })


def test_validate_providers_config_rejects_self_loop_provider():
    with pytest.raises(ValueError, match="请求循环"):
        validate_providers_config({
            "gateway": {"host": "127.0.0.1", "port": 11434},
            "providers": [
                {"id": "p", "base_url": "http://127.0.0.1:11434"},
            ],
        })


def test_validate_providers_config_noop_on_non_dict_or_empty():
    validate_providers_config(None)       # no raise
    validate_providers_config({})         # no providers
    validate_providers_config({"providers": []})


def test_validate_providers_config_skips_provider_without_base_url():
    """A provider missing base_url is skipped (not a validation error here)."""
    validate_providers_config({"providers": [{"id": "p"}]})  # no raise


def test_validate_providers_config_skips_non_dict_provider():
    """A non-dict provider entry is skipped, not crashed on."""
    validate_providers_config({"providers": ["not-a-dict", 42]})  # no raise


def test_non_ip_resolve_result_skipped():
    """A non-IP string returned by _resolve is skipped (defensive continue)."""
    with patch("llmport.config.validation.socket.getaddrinfo") as mock_gai:
        mock_gai.return_value = [(None, None, None, None, ("not-an-ip", 0))]
        # No blocked IP among resolved addrs -> allowed.
        validate_provider_base_url("http://weird.example", "127.0.0.1", 11434)


# ── save_providers_config chokepoint ─────────────────────────────────────

def test_save_providers_config_rejects_metadata_base_url(tmp_path):
    """ConfigStore.save_providers_config is the single chokepoint that guards writes."""
    from llmport.config.store import ConfigStore
    store = ConfigStore(str(tmp_path / "llmport"))
    store.init_first_run()
    with pytest.raises(ValueError, match="SSRF"):
        store.save_providers_config({
            "version": 1,
            "gateway": {"host": "127.0.0.1", "port": 11434},
            "providers": [{"id": "p", "base_url": "http://169.254.169.254"}],
        })


def test_save_providers_config_allows_local_base_url(tmp_path):
    from llmport.config.store import ConfigStore
    store = ConfigStore(str(tmp_path / "llmport"))
    store.init_first_run()
    store.save_providers_config({
        "version": 1,
        "gateway": {"host": "127.0.0.1", "port": 11434},
        "providers": [{"id": "p", "base_url": "http://127.0.0.1:11435"}],
    })  # no raise

