"""Bounded HTTPS transport for public SST data, with safe error summaries.

This module performs one request per call. Retry policy belongs to the caller.
It has no model API access and never disables TLS certificate verification.
"""
from __future__ import annotations

import math
import http.client
import os
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
CHUNK_BYTES = 64 * 1024


class DownloadCancelled(Exception):
    pass


class NetworkDeadlineExceeded(Exception):
    pass


class DataResponseError(ValueError):
    """The response is not acceptable scientific input; do not blindly retry."""


class NetworkConfigurationError(ValueError):
    def __init__(self, category):
        self.category = category
        super().__init__("数据网络配置无效，请检查代理或证书设置。")


class UnsafeRedirectError(DataResponseError):
    pass


def _check_state(stop, deadline):
    if stop.is_set():
        raise DownloadCancelled("数据下载已取消。")
    if deadline is not None and time.monotonic() >= deadline:
        raise NetworkDeadlineExceeded("本次数据下载的时间预算已用完。")


def _request_timeout(timeout, stop, deadline):
    _check_state(stop, deadline)
    return min(timeout, max(0.001, deadline - time.monotonic())) if deadline is not None else timeout


def tls_context():
    """Preserve default/system trust, then add public and optional local CAs."""
    try:
        context = ssl.create_default_context()
        try:
            import certifi
        except ImportError:
            pass
        else:
            context.load_verify_locations(cafile=certifi.where())
        bundle = os.environ.get("SCI_DSH_DATA_CA_BUNDLE", "").strip()
        if bundle:
            context.load_verify_locations(cafile=bundle)
        # NOAA PSL's public TDS requests a legacy TLS renegotiation sequence
        # on some Windows paths. This option keeps certificate verification
        # enabled while allowing that server-side handshake when the explicit
        # PSL fallback is selected.
        if os.environ.get("SCI_DSH_DATA_SOURCE", "").strip().lower() == "psl-monthly":
            legacy = getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0)
            if legacy:
                context.options |= legacy
        return context
    except (OSError, ssl.SSLError, ValueError):
        raise NetworkConfigurationError("tls_config") from None


def _https_url(url):
    try:
        parsed = urllib.parse.urlsplit(url)
        valid = (parsed.scheme == "https" and parsed.hostname and not parsed.username
                 and not parsed.password and not parsed.fragment)
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            valid = False
        return bool(valid)
    except (TypeError, ValueError):
        return False


class HTTPSOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _https_url(newurl):
            raise UnsafeRedirectError("数据源重定向到了不允许的地址。")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _proxy_handler():
    proxy = os.environ.get("SCI_DSH_DATA_PROXY", "").strip()
    if proxy.lower() in {"direct", "none", "off"}:
        # Explicitly bypass inherited system proxy variables. This keeps the
        # data path deterministic when a local proxy cannot tunnel NOAA TLS.
        return urllib.request.ProxyHandler({})
    if not proxy:
        # ProxyHandler's default reads standard proxy variables/system settings.
        return urllib.request.ProxyHandler()
    try:
        parsed = urllib.parse.urlsplit(proxy)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
                or (parsed.port is not None and not 1 <= parsed.port <= 65535)):
            raise ValueError
    except ValueError:
        raise NetworkConfigurationError("proxy_config") from None
    return urllib.request.ProxyHandler({"http": proxy, "https": proxy})


def _set_read_timeout(response, timeout):
    # urllib's HTTPS response exposes the connection socket through its buffered
    # reader. Updating it bounds each read by the remaining overall budget.
    raw = getattr(getattr(response, "fp", None), "raw", None)
    connection = getattr(raw, "_sock", None)
    if connection is not None:
        connection.settimeout(timeout)


def fetch_bytes(url, timeout, stop, *, deadline=None):
    """Fetch one HTTPS response; respect cancellation, time budget and size cap.

    Cancellation is checked between blocking operations. Connect/read use a
    finite timeout; system DNS resolution can additionally depend on OS limits.
    """
    if not _https_url(url):
        raise NetworkConfigurationError("url_config")
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise NetworkConfigurationError("timeout_config")
    if deadline is not None and (not isinstance(deadline, (int, float)) or not math.isfinite(deadline)):
        raise NetworkConfigurationError("timeout_config")
    _check_state(stop, deadline)
    opener = urllib.request.build_opener(
        _proxy_handler(), urllib.request.HTTPSHandler(context=tls_context()), HTTPSOnlyRedirect())
    request = urllib.request.Request(url, headers={
        "User-Agent": "Scientific-Agent-OISST-Teaching-Demo/1.1",
        "Accept": "text/csv, text/plain;q=0.9, */*;q=0.1",
        "Accept-Encoding": "identity",
    })
    with opener.open(request, timeout=_request_timeout(timeout, stop, deadline)) as response:
        if not _https_url(response.geturl()):
            raise UnsafeRedirectError("数据响应的最终地址不是允许的 HTTPS 地址。")
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                declared = int(content_length)
            except ValueError:
                raise DataResponseError("数据响应的长度声明无效。") from None
            if declared < 0 or declared > MAX_RESPONSE_BYTES:
                raise DataResponseError("数据响应超过允许大小。")
        # read1 performs at most one buffered/raw read, letting us check the
        # deadline even when a server keeps sending a slow trickle of bytes.
        read = getattr(response, "read1", response.read)
        parts, size = [], 0
        while True:
            _set_read_timeout(response, _request_timeout(timeout, stop, deadline))
            chunk = read(min(CHUNK_BYTES, MAX_RESPONSE_BYTES - size + 1))
            _check_state(stop, deadline)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise DataResponseError("数据响应超过允许大小。")
            parts.append(chunk)
        return b"".join(parts)


def classify_error(exc):
    """Return fixed, JSON-safe messages; never echo URLs, credentials or errors."""
    def summary(category, message, retryable, action, status=None):
        result = {"category": category, "message": message, "retryable": retryable, "action": action}
        if status is not None:
            result["http_status"] = status
        return result

    if isinstance(exc, DownloadCancelled):
        return summary("cancelled", "数据下载已取消。", False, "已成功的缓存保留；需要时重新准备数据。")
    if isinstance(exc, NetworkDeadlineExceeded):
        return summary("deadline", "本次下载时间预算已用完。", False, "保留缓存，确认网络可用后继续准备数据。")
    if isinstance(exc, NetworkConfigurationError):
        actions = {
            "proxy_config": "检查 SCI_DSH_DATA_PROXY，仅支持 HTTP/HTTPS 代理地址。",
            "tls_config": "检查系统证书和 SCI_DSH_DATA_CA_BUNDLE 指定的 CA 文件；不要关闭证书校验。",
            "url_config": "检查数据源地址，必须是无登录凭据的 HTTPS 地址。",
            "timeout_config": "下载超时参数必须是正数，时间预算必须有效。",
        }
        return summary(exc.category, "数据网络配置无效。", False, actions.get(exc.category, "检查数据下载配置。"))
    if isinstance(exc, UnsafeRedirectError):
        return summary("unsafe_redirect", "数据源重定向到了不允许的地址。", False, "检查数据源或代理，不能降级到 HTTP。")
    if isinstance(exc, DataResponseError):
        return summary("data_response", "返回内容不是符合要求的数据响应。", False, "检查数据源是否返回错误页、登录页、过大响应或不兼容 CSV；不要将其当作海温数据。")
    if isinstance(exc, urllib.error.HTTPError):
        status = exc.code
        if status == 407:
            return summary("proxy_auth", "数据代理要求认证。", False, "检查本机代理设置和认证，不要公开代理凭据。", status)
        if status in (408, 429) or 500 <= status <= 599:
            return summary("http_transient", "数据服务器暂时不可用或限制了请求。", True, "稍后重试，降低并发并复用缓存。", status)
        return summary("http_error", "数据服务器拒绝了请求或资源不存在。", False, "根据 HTTP 状态核对数据源和访问权限；反复增加超时无效。", status)
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, ssl.SSLError):
        return summary("tls", "HTTPS 证书校验或 TLS 连接失败。", False, "检查系统时间、信任证书及代理；机构网络可配置 SCI_DSH_DATA_CA_BUNDLE，不能关闭验证。")
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return summary("timeout", "连接数据源或读取数据超时。", True, "确认网络/代理可访问数据源，降低并发并继续使用缓存。")
    if isinstance(reason, socket.gaierror):
        return summary("dns", "无法解析数据源地址。", True, "检查网络、DNS 或数据代理配置。")
    # urllib can wrap proxy tunnel failures as OSError, without HTTPError.
    # Inspect only known markers; never return the original exception text.
    reason_text = str(reason).lower()
    if "tunnel connection failed: 407" in reason_text:
        return summary("proxy_auth", "数据代理要求认证。", False, "检查本机代理设置和认证，不要公开代理凭据。", 407)
    if "certificate_verify_failed" in reason_text or "certificate verify failed" in reason_text:
        return summary("tls", "HTTPS 证书验证失败。", False, "检查系统时间、系统 CA 或机构 CA 配置；不要关闭证书验证。")
    if "timed out" in reason_text:
        return summary("timeout", "连接数据源或读取数据超时。", True, "确认网络/代理可访问数据源，降低并发并复用缓存。")
    if isinstance(exc, urllib.error.URLError) or isinstance(reason, (ConnectionError, http.client.IncompleteRead)):
        return summary("connection", "无法连接数据源或连接被中断。", True, "检查本机网络和代理；可以稍后重试并保留已完成缓存。")
    if isinstance(reason, OSError):
        return summary("io", "下载过程发生本地或网络 I/O 错误。", False, "检查网络、磁盘空间和目录权限，保留诊断报告。")
    return summary("unexpected", "下载过程发生未分类错误。", False, "保留诊断报告并提供错误类别；不要公开配置或凭据。")
