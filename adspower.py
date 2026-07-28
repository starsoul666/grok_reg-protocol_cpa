#!/usr/bin/env python3
"""AdsPower Local API client + DrissionPage attach helpers.

Default Local API: http://local.adspower.net:50325 (also 127.0.0.1:50325).
Docs: https://localapi-doc-en.adspower.com/

Free-tier constraints this module respects:
- QPS <= 1 across the whole client process → shared throttle lock.
- Do NOT auto-create/delete profiles by default (profile slots are limited);
  the recommended usage is a fixed adspower_user_ids pool.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any
from urllib.parse import urlparse


DEFAULT_API = "http://local.adspower.net:50325"


class AdsPowerError(RuntimeError):
    pass


# ── process-wide rate limiter (free tier: <= 1 QPS) ──────────────────────────
_rate_lock = threading.Lock()
_last_call_ts: float = 0.0


def _throttle(min_interval: float) -> None:
    """Serialize AdsPower Local API calls to stay under free-tier QPS."""
    if min_interval <= 0:
        return
    global _last_call_ts
    with _rate_lock:
        wait = min_interval - (time.time() - _last_call_ts)
        if wait > 0:
            time.sleep(wait)
        _last_call_ts = time.time()


class AdsPowerClient:
    def __init__(
        self,
        api_base: str = DEFAULT_API,
        *,
        api_key: str = "",
        timeout: float = 60.0,
        rate_limit_sec: float = 1.05,
    ):
        self.api_base = (api_base or DEFAULT_API).rstrip("/")
        self.api_key = (api_key or "").strip()
        self.timeout = float(timeout or 60.0)
        self.rate_limit_sec = float(rate_limit_sec or 0.0)

    def _request(self, method: str, path: str, *, params: dict | None = None, body: dict | None = None) -> dict:
        _throttle(self.rate_limit_sec)
        url = f"{self.api_base}{path if path.startswith('/') else '/' + path}"
        if params:
            # AdsPower expects JSON-encoded array/object query values.
            flat: list[tuple[str, str]] = []
            for k, v in params.items():
                if v is None:
                    continue
                if isinstance(v, (list, tuple, dict)):
                    flat.append((k, json.dumps(v, ensure_ascii=False)))
                elif isinstance(v, bool):
                    flat.append((k, "1" if v else "0"))
                else:
                    flat.append((k, str(v)))
            if flat:
                url = f"{url}?{urllib.parse.urlencode(flat)}"
        data = None
        headers: dict[str, str] = {}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.URLError as exc:
            raise AdsPowerError(
                f"AdsPower Local API 不可达 {url}: {exc}. 请确认 AdsPower 客户端已启动且 Local API 端口正确（默认 50325）"
            ) from exc
        except Exception as exc:
            raise AdsPowerError(f"AdsPower API 请求失败 {url}: {exc}") from exc

        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise AdsPowerError(f"AdsPower API 返回非 JSON: {raw[:200]}") from exc
        if not isinstance(payload, dict):
            raise AdsPowerError(f"AdsPower API 返回异常: {payload!r}")
        code = payload.get("code")
        if code != 0:
            raise AdsPowerError(payload.get("msg") or payload.get("message") or str(payload))
        return payload

    def get(self, path: str, params: dict | None = None) -> dict:
        return self._request("GET", path, params=params)

    def post(self, path: str, body: dict | None = None) -> dict:
        return self._request("POST", path, body=body)

    # ── endpoints ──
    def health(self) -> bool:
        try:
            self.get("/status")
            return True
        except Exception:
            return False

    def list_users(self, *, page: int = 1, page_size: int = 100, name: str | None = None) -> dict:
        params: dict[str, Any] = {"page": max(1, int(page)), "page_size": min(int(page_size), 100)}
        if name:
            params["group_name"] = name  # kept for parity; group filter is optional
        data = self.get("/api/v1/user/list", params).get("data") or {}
        return data if isinstance(data, dict) else {}

    def find_by_name(self, name: str) -> str | None:
        name = (name or "").strip()
        if not name:
            return None
        try:
            data = self.list_users(page=1, page_size=100)
        except AdsPowerError:
            return None
        for item in data.get("list") or []:
            n = str(item.get("name") or "").strip()
            if n == name:
                uid = str(item.get("user_id") or "").strip()
                if uid:
                    return uid
        for item in data.get("list") or []:
            if name in str(item.get("name") or ""):
                uid = str(item.get("user_id") or "").strip()
                if uid:
                    return uid
        return None

    def start(
        self,
        user_id: str,
        *,
        launch_args: list[str] | None = None,
        headless: bool = False,
        open_tabs: int | None = None,
        clear_cache_after_closing: bool = False,
        ip_tab: bool | None = None,
    ) -> dict:
        params: dict[str, Any] = {"user_id": user_id}
        if launch_args:
            params["launch_args"] = list(launch_args)
        if headless:
            params["headless"] = 1
        if open_tabs is not None:
            params["open_tabs"] = int(open_tabs)
        if clear_cache_after_closing:
            params["clear_cache_after_closing"] = 1
        if ip_tab is not None:
            params["ip_tab"] = 1 if ip_tab else 0
        data = self.get("/api/v1/browser/start", params).get("data") or {}
        if not isinstance(data, dict):
            raise AdsPowerError(f"AdsPower start 未返回 data: {data!r}")
        ws = data.get("ws") or {}
        selenium = (ws or {}).get("selenium") if isinstance(ws, dict) else None
        if not selenium:
            raise AdsPowerError(f"AdsPower start 未返回 ws.selenium 调试地址: {data!r}")
        return data

    def stop(self, user_id: str) -> None:
        try:
            self.get("/api/v1/browser/stop", {"user_id": user_id})
        except AdsPowerError:
            # tolerate "already stopped"
            pass

    def active(self, user_id: str) -> dict:
        data = self.get("/api/v1/browser/active", {"user_id": user_id}).get("data") or {}
        return data if isinstance(data, dict) else {}


def parse_proxy_for_adspower(proxy: str) -> dict[str, Any]:
    """Map config proxy URL to AdsPower user_proxy_config fields."""
    proxy = (proxy or "").strip()
    if not proxy:
        return {"proxy_soft": "no_proxy"}
    u = urlparse(proxy if "://" in proxy else f"http://{proxy}")
    host = u.hostname or ""
    if not host:
        return {"proxy_soft": "no_proxy"}
    scheme = (u.scheme or "http").lower()
    if scheme in ("socks5", "socks5h"):
        proxy_type = "socks5"
    elif scheme == "https":
        proxy_type = "https"
    else:
        proxy_type = "http"
    port = u.port or (443 if proxy_type == "https" else 80)
    out: dict[str, Any] = {
        "proxy_soft": "other",
        "proxy_type": proxy_type,
        "proxy_host": host,
        "proxy_port": int(port),
    }
    if u.username:
        out["proxy_user"] = u.username
    if u.password:
        out["proxy_password"] = u.password
    return out


class AdsPowerIdPool:
    """Claim/release fixed user_ids across worker threads (free tier friendly)."""

    def __init__(self, ids: list[str]):
        self._ids = [i.strip() for i in ids if str(i).strip()]
        self._lock = threading.Lock()
        self._in_use: set[str] = set()

    def claim(self, preferred: str | None = None) -> str:
        with self._lock:
            if preferred and preferred in self._ids and preferred not in self._in_use:
                self._in_use.add(preferred)
                return preferred
            for uid in self._ids:
                if uid not in self._in_use:
                    self._in_use.add(uid)
                    return uid
        raise AdsPowerError(
            f"AdsPower 环境池已耗尽（共 {len(self._ids)} 个）。"
            "请增加 adspower_user_ids，或把 register_threads 调低"
        )

    def release(self, user_id: str) -> None:
        with self._lock:
            self._in_use.discard(user_id)


_pool_lock = threading.Lock()
_id_pool: AdsPowerIdPool | None = None


def reset_id_pool() -> None:
    global _id_pool
    with _pool_lock:
        _id_pool = None


def get_id_pool(ids: list[str]) -> AdsPowerIdPool:
    global _id_pool
    with _pool_lock:
        if _id_pool is None:
            _id_pool = AdsPowerIdPool(ids)
        return _id_pool


def build_launch_args(
    *,
    extension_path: str | None = None,
    extra_args: list[str] | None = None,
) -> list[str]:
    """AdsPower forwards launch_args verbatim; keep it minimal."""
    args: list[str] = []
    if extension_path:
        args.append(f"--load-extension={extension_path}")
    if extra_args:
        for a in extra_args:
            a = str(a).strip()
            if a and a not in args:
                args.append(a)
    return args


def resolve_user_id(client: AdsPowerClient, cfg: dict, log=None) -> tuple[str, dict]:
    """Resolve which AdsPower profile to open.

    Returns (user_id, meta). Free tier keeps things simple: prefer fixed pool,
    fall back to name lookup, never auto-create.
    """
    cfg = cfg or {}
    meta: dict[str, Any] = {"from_pool": False}

    ids_raw = cfg.get("adspower_user_ids") or []
    if isinstance(ids_raw, str):
        ids = [x.strip() for x in ids_raw.split(",") if x.strip()]
    else:
        ids = [str(x).strip() for x in ids_raw if str(x).strip()]

    single = str(cfg.get("adspower_user_id") or "").strip()
    if single and single not in ids:
        ids = [single, *ids]

    if ids:
        pool = get_id_pool(ids)
        uid = pool.claim()
        meta["from_pool"] = True
        meta["pool_size"] = len(ids)
        if log:
            log(f"[adspower] 使用环境池 user_id={uid} ({len(ids)} 个)")
        return uid, meta

    name = str(cfg.get("adspower_name") or "").strip()
    if name:
        found = client.find_by_name(name)
        if found:
            if log:
                log(f"[adspower] 按名称命中 name={name} user_id={found}")
            return found, meta
        raise AdsPowerError(f"未找到名为 {name!r} 的 AdsPower 环境（免费版不支持自动创建）")

    raise AdsPowerError(
        "未配置 adspower_user_id / adspower_user_ids / adspower_name，无法启动 AdsPower 环境"
    )


def open_and_attach(
    cfg: dict | None = None,
    *,
    extension_path: str | None = None,
    log=None,
):
    """Open an AdsPower profile and attach DrissionPage Chromium to its CDP.

    Returns (browser, meta) where meta carries user_id / http / client.
    """
    from DrissionPage import Chromium, ChromiumOptions

    cfg = dict(cfg or {})
    api = str(cfg.get("adspower_api") or DEFAULT_API).strip() or DEFAULT_API
    timeout = float(cfg.get("adspower_timeout") or 60)
    rate = float(cfg.get("adspower_rate_limit_sec") or 1.05)
    api_key = str(cfg.get("adspower_api_key") or "").strip()
    client = AdsPowerClient(api_base=api, api_key=api_key, timeout=timeout, rate_limit_sec=rate)

    if not client.health():
        raise AdsPowerError(f"AdsPower Local API 不健康: {api}（请确认客户端已启动并在设置中开启 API）")

    user_id, meta = resolve_user_id(client, cfg, log=log)

    extra_args = cfg.get("adspower_launch_args") or cfg.get("adspower_args") or []
    if isinstance(extra_args, str):
        extra_args = [extra_args]
    use_turnstile = bool(cfg.get("adspower_load_turnstile_patch", True))
    ext = extension_path if use_turnstile else None
    # AdsPower headless: honor explicit adspower_headless, else fall back to
    # register_headless. Turnstile 通过率有头更高。
    if "adspower_headless" in cfg:
        headless = bool(cfg.get("adspower_headless"))
    else:
        headless = bool(cfg.get("register_headless", False))
    launch_args = build_launch_args(extension_path=ext, extra_args=list(extra_args))
    open_tabs_raw = cfg.get("adspower_open_tabs")
    open_tabs = int(open_tabs_raw) if isinstance(open_tabs_raw, (int, str)) and str(open_tabs_raw).strip() not in ("", "0") else None

    # best-effort stop leftover
    try:
        client.stop(user_id)
    except Exception:
        pass

    started = client.start(
        user_id,
        launch_args=launch_args or None,
        headless=headless,
        open_tabs=open_tabs,
    )
    ws = started.get("ws") or {}
    http_addr = (ws or {}).get("selenium") if isinstance(ws, dict) else None
    http_addr = str(http_addr or "").strip()
    debug_port = str(started.get("debug_port") or "").strip()
    if log:
        log(f"[adspower] opened user_id={user_id} http={http_addr} pid={started.get('pid')} debug_port={debug_port}")

    co = ChromiumOptions()
    co.set_address(http_addr)
    co.existing_only(True)
    try:
        co.set_timeouts(base=1)
    except Exception:
        pass

    last_exc: Exception | None = None
    browser = None
    for attempt in range(1, 6):
        try:
            browser = Chromium(co)
            break
        except Exception as exc:
            last_exc = exc
            time.sleep(0.4 * attempt)
    if browser is None:
        try:
            client.stop(user_id)
        except Exception:
            pass
        if meta.get("from_pool"):
            try:
                if _id_pool is not None:
                    _id_pool.release(user_id)
            except Exception:
                pass
        raise AdsPowerError(f"DrissionPage 接管 AdsPower 失败 ({http_addr}): {last_exc}")

    meta.update(
        {
            "user_id": user_id,
            "http": http_addr,
            "ws": ws,
            "pid": started.get("pid"),
            "debug_port": debug_port,
            "webdriver": started.get("webdriver"),
            "client": client,
        }
    )
    try:
        browser._adspower_meta = meta  # type: ignore[attr-defined]
        browser._adspower_id = user_id  # type: ignore[attr-defined]
    except Exception:
        pass
    return browser, meta


def release_attached(browser, *, log=None) -> None:
    """Stop AdsPower window via API and free the pool slot. Never delete profile."""
    meta = getattr(browser, "_adspower_meta", None) or {}
    user_id = str(getattr(browser, "_adspower_id", None) or meta.get("user_id") or "").strip()
    client: AdsPowerClient | None = meta.get("client")
    if client is None and user_id:
        try:
            client = AdsPowerClient()
        except Exception:
            client = None

    if user_id and client is not None:
        try:
            client.stop(user_id)
            if log:
                log(f"[adspower] stopped user_id={user_id}")
        except Exception as exc:
            if log:
                log(f"[adspower] stop 失败: {exc}")
        if meta.get("from_pool"):
            try:
                if _id_pool is not None:
                    _id_pool.release(user_id)
            except Exception:
                pass

    # AdsPower owns user-data; best-effort disconnect only.
    try:
        quit_fn = getattr(browser, "quit", None)
        if callable(quit_fn):
            try:
                quit_fn(timeout=2, force=True, del_data=False)
            except TypeError:
                try:
                    quit_fn()
                except Exception:
                    pass
    except Exception:
        pass
