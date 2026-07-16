"""Probe free Grok 4.5 via cli-chat-proxy with a CPA access_token."""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any

from .proxyutil import resolve_proxy
from .schema import DEFAULT_BASE_URL, DEFAULT_CLIENT_HEADERS

# Wait before the first chat probe — new tokens often deny immediately after mint.
DEFAULT_CHAT_PROBE_INITIAL_DELAY_SEC: float = 3.0
# After a failed attempt, wait these seconds before each retry.
DEFAULT_CHAT_PROBE_RETRY_DELAYS_SEC: tuple[float, ...] = (5.0, 15.0, 30.0)


def _sleep_interruptible(
    seconds: float,
    *,
    cancel: Callable[[], bool] | None = None,
) -> bool:
    """Sleep up to `seconds`. Return True if cancelled."""
    if seconds <= 0:
        return bool(cancel and cancel())
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cancel and cancel():
            return True
        time.sleep(min(0.5, end - time.monotonic()))
    return bool(cancel and cancel())


def _ssl_context() -> ssl.SSLContext | None:
    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


def _opener(proxy: str | None = None) -> urllib.request.OpenerDirector:
    p = resolve_proxy(proxy)
    handlers: list[Any] = []
    ctx = _ssl_context()
    if ctx is not None:
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
    if p:
        handlers.append(urllib.request.ProxyHandler({"http": p, "https": p}))
    return urllib.request.build_opener(*handlers) if handlers else urllib.request.build_opener()


def probe_models(
    access_token: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = 30.0,
    proxy: str | None = None,
) -> dict[str, Any]:
    base = base_url.rstrip("/")
    url = f"{base}/models"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        **DEFAULT_CLIENT_HEADERS,
    }
    opener = _opener(proxy)
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            ids = [x.get("id") for x in body.get("data") or [] if isinstance(x, dict)]
            return {
                "ok": True,
                "status": getattr(resp, "status", 200),
                "model_ids": ids,
                "has_grok_45": any(i == "grok-4.5" for i in ids),
            }
    except urllib.error.HTTPError as e:
        return {
            "ok": False,
            "status": e.code,
            "error": e.read().decode("utf-8", errors="replace")[:500],
            "model_ids": [],
            "has_grok_45": False,
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "status": 0,
            "error": str(e),
            "model_ids": [],
            "has_grok_45": False,
        }


def probe_mini_response(
    access_token: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = 60.0,
    proxy: str | None = None,
) -> dict[str, Any]:
    base = base_url.rstrip("/")
    url = f"{base}/responses"
    payload = {
        "model": "grok-4.5",
        "stream": False,
        "input": "Reply with exactly MINT_OK",
        "reasoning": {"effort": "low"},
    }
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        **DEFAULT_CLIENT_HEADERS,
    }
    opener = _opener(proxy)
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            texts: list[str] = []
            for item in body.get("output") or []:
                if item.get("type") == "message":
                    for c in item.get("content") or []:
                        if c.get("type") == "output_text":
                            texts.append(c.get("text") or "")
            return {
                "ok": True,
                "status": getattr(resp, "status", 200),
                "model": body.get("model"),
                "text": "\n".join(texts),
                "usage": body.get("usage"),
            }
    except urllib.error.HTTPError as e:
        return {
            "ok": False,
            "status": e.code,
            "error": e.read().decode("utf-8", errors="replace")[:800],
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "error": str(e)}


def probe_mini_response_with_retry(
    access_token: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = 60.0,
    proxy: str | None = None,
    initial_delay_sec: float | None = None,
    retry_delays_sec: Sequence[float] | None = None,
    log: Callable[[str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Probe chat with pre-delay + delayed retries for new-account permission lag.

    Default: wait 3s before first attempt, then retry after 5s / 15s / 30s.
    Attempts = 1 + len(retry_delays).
    """
    init_delay = (
        DEFAULT_CHAT_PROBE_INITIAL_DELAY_SEC
        if initial_delay_sec is None
        else max(0.0, float(initial_delay_sec))
    )
    delays = (
        DEFAULT_CHAT_PROBE_RETRY_DELAYS_SEC
        if retry_delays_sec is None
        else tuple(float(x) for x in retry_delays_sec if float(x) >= 0)
    )
    attempts = 1 + len(delays)
    last: dict[str, Any] = {"ok": False, "status": 0, "error": "chat probe not attempted"}
    _log = log or (lambda _m: None)

    if init_delay > 0:
        _log(f"probe chat: wait {init_delay:g}s before first attempt")
        if _sleep_interruptible(init_delay, cancel=cancel):
            return {
                "ok": False,
                "status": 0,
                "error": "cancelled during chat probe initial wait",
                "attempts": 0,
                "attempted": 0,
            }

    for i in range(attempts):
        if cancel and cancel():
            last = {
                "ok": False,
                "status": 0,
                "error": "cancelled",
                "attempts": i,
                "attempted": i,
            }
            return last

        last = probe_mini_response(
            access_token,
            base_url=base_url,
            timeout=timeout,
            proxy=proxy,
        )
        last["attempts"] = i + 1
        last["attempted"] = i + 1
        if last.get("ok"):
            if i > 0:
                _log(f"probe chat retry success on attempt {i + 1}/{attempts}")
            return last

        err = str(last.get("error") or last.get("status") or "unknown")[:200]
        if i >= len(delays):
            _log(f"probe chat failed after {attempts} attempt(s): {err}")
            break

        wait = delays[i]
        _log(
            f"probe chat attempt {i + 1}/{attempts} failed ({err}); "
            f"retry in {wait:g}s"
        )
        if _sleep_interruptible(wait, cancel=cancel):
            return {
                "ok": False,
                "status": 0,
                "error": "cancelled during chat probe retry wait",
                "attempts": i + 1,
                "attempted": i + 1,
            }

    return last
