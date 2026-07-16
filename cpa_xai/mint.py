"""High-level: mint CPA xai-*.json for one free registered account."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Callable

from .browser_confirm import mint_with_browser
from .pkce_mint import PKCEMintError, mint_with_sso_pkce
from .probe import (
    DEFAULT_CHAT_PROBE_INITIAL_DELAY_SEC,
    DEFAULT_CHAT_PROBE_RETRY_DELAYS_SEC,
    probe_mini_response_with_retry,
    probe_models,
)
from .protocol_mint import ProtocolMintError, extract_sso_from_cookies, mint_with_sso_protocol
from .proxyutil import proxy_log_label, resolve_proxy, set_runtime_proxy
from .schema import DEFAULT_BASE_URL, build_cpa_xai_auth
from .writer import write_cpa_xai_auth

LogFn = Callable[[str], None]


def _noop(_: str) -> None:
    return None


def mint_and_export(
    *,
    email: str,
    password: str,
    auth_dir: str | Path,
    page: Any | None = None,
    proxy: str | None = None,
    headless: bool = False,
    base_url: str = DEFAULT_BASE_URL,
    probe: bool = True,
    probe_chat: bool = False,
    probe_chat_initial_delay_sec: float | None = None,
    probe_chat_retry_delays_sec: Sequence[float] | None = None,
    browser_timeout_sec: float = 240.0,
    force_standalone: bool = True,
    cookies: Any | None = None,
    sso: str | None = None,
    reuse_browser: bool = True,
    recycle_every: int = 15,
    prefer_protocol: bool = True,
    protocol_only: bool = False,
    protocol_poll_timeout_sec: float = 90.0,
    allow_device_flow_fallback: bool = False,
    protocol_flow: str = "pkce",
    log: LogFn | None = None,
    cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Full pipeline: (protocol SSO device-flow |) browser device-auth → write CPA → probe.

    Protocol path (curl_cffi + sso cookie) is tried first when prefer_protocol
    and an sso cookie is available. On failure, falls back to browser mint unless
    protocol_only=True.

    Returns dict with keys: ok, path, email, probe, error?, mint_method?
    """
    log = log or _noop
    email = (email or "").strip()
    protocol_flow = (protocol_flow or "pkce").strip().lower()
    if protocol_flow not in {"pkce", "device"}:
        return {
            "ok": False,
            "email": email,
            "error": f"unsupported cpa_protocol_flow: {protocol_flow}; expected pkce or device",
        }
    if not email or not password:
        # Protocol can work with sso alone; password only required for browser fallback
        if not email:
            return {"ok": False, "email": email, "error": "missing email"}
        if not (sso or extract_sso_from_cookies(cookies)):
            return {"ok": False, "email": email, "error": "missing email/password"}

    # Config/explicit proxy wins over shell https_proxy (common 7890 trap).
    # Thread-local pin — safe under concurrent mint workers.
    resolved = resolve_proxy(proxy)
    set_runtime_proxy(resolved or None)
    log(f"mint start: {email} proxy={proxy_log_label(resolved) or '(none)'}")

    sso_val = (sso or "").strip() or extract_sso_from_cookies(cookies)
    tokens: dict[str, Any] | None = None
    protocol_err: str | None = None

    if prefer_protocol and sso_val:
        if protocol_flow == "pkce":
            log("mint try protocol (SSO HTTP PKCE authorization-code flow)")
            try:
                tokens = mint_with_sso_pkce(
                    sso_cookie=sso_val,
                    email=email,
                    proxy=resolved or None,
                    log=log,
                    cancel=cancel,
                )
                log("mint protocol PKCE SUCCESS")
            except PKCEMintError as e:
                protocol_err = str(e)
                log(f"mint protocol PKCE failed: {e}")
                if allow_device_flow_fallback:
                    log("mint fallback → device flow")
            except Exception as e:  # noqa: BLE001
                protocol_err = str(e)
                log(f"mint protocol PKCE exception: {e}")
                if allow_device_flow_fallback:
                    log("mint fallback → device flow")

            if tokens is None and not allow_device_flow_fallback:
                return {
                    "ok": False,
                    "email": email,
                    "error": f"pkce protocol failed: {protocol_err}",
                    "mint_method": "pkce",
                }
        else:
            log("mint try protocol (SSO HTTP device flow)")

        if tokens is None:
            try:
                tokens = mint_with_sso_protocol(
                    sso_cookie=sso_val,
                    email=email,
                    proxy=resolved or None,
                    poll_timeout_sec=protocol_poll_timeout_sec,
                    log=log,
                    cancel=cancel,
                )
                log("mint protocol device-flow SUCCESS")
            except ProtocolMintError as e:
                device_err = str(e)
                log(f"mint protocol device-flow failed: {e}")
                protocol_err = f"pkce: {protocol_err}; device: {device_err}" if protocol_err else device_err
                if protocol_only:
                    return {
                        "ok": False,
                        "email": email,
                        "error": f"protocol_only: {protocol_err}",
                        "mint_method": "protocol",
                    }
                log("mint fallback → browser")
            except Exception as e:  # noqa: BLE001
                device_err = str(e)
                log(f"mint protocol device-flow exception: {e}")
                protocol_err = f"pkce: {protocol_err}; device: {device_err}" if protocol_err else device_err
                if protocol_only:
                    return {
                        "ok": False,
                        "email": email,
                        "error": f"protocol_only: {protocol_err}",
                        "mint_method": "protocol",
                    }
                log("mint fallback → browser")
    elif prefer_protocol and not sso_val:
        log("mint protocol skipped (no sso cookie) → browser")
        if protocol_only:
            return {
                "ok": False,
                "email": email,
                "error": "protocol_only but no sso cookie",
                "mint_method": "protocol",
            }
    elif not prefer_protocol:
        log("mint protocol disabled → browser")

    if tokens is None:
        if not password:
            return {
                "ok": False,
                "email": email,
                "error": protocol_err or "protocol failed and no password for browser fallback",
                "protocol_error": protocol_err,
            }
        try:
            tokens = mint_with_browser(
                email=email,
                password=password,
                page=None if force_standalone else page,
                proxy=resolved or None,
                headless=headless,
                browser_timeout_sec=browser_timeout_sec,
                force_standalone=force_standalone,
                cookies=cookies,
                reuse_browser=reuse_browser,
                recycle_every=recycle_every,
                poll_log=log,
                cancel=cancel,
            )
            tokens["mint_method"] = "browser"
            if protocol_err:
                tokens["protocol_error"] = protocol_err
        except Exception as e:  # noqa: BLE001
            log(f"mint failed: {e}")
            err = str(e)
            if protocol_err:
                err = f"{err} (protocol: {protocol_err})"
            return {
                "ok": False,
                "email": email,
                "error": err,
                "protocol_error": protocol_err,
            }

    payload = build_cpa_xai_auth(
        email=email,
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        id_token=tokens.get("id_token"),
        expires_in=tokens.get("expires_in"),
        base_url=base_url,
    )
    path = write_cpa_xai_auth(auth_dir, payload)
    log(f"wrote {path}")

    result: dict[str, Any] = {
        "ok": True,
        "email": email,
        "path": str(path),
        "user_code": tokens.get("user_code"),
        "base_url": base_url,
        "proxy": proxy_log_label(resolved),
        "mint_method": tokens.get("mint_method") or "browser",
    }
    if protocol_err and result["mint_method"] != "protocol":
        result["protocol_error"] = protocol_err

    if probe:
        pr = probe_models(tokens["access_token"], base_url=base_url, proxy=resolved or None)
        result["probe_models"] = pr
        log(
            f"probe models: ok={pr.get('ok')} status={pr.get('status')} "
            f"has_grok_45={pr.get('has_grok_45')} ids={pr.get('model_ids')} "
            f"error={str(pr.get('error') or '')[:200]}"
        )
        if not pr.get("has_grok_45"):
            result["ok"] = False
            result["error"] = "token ok but grok-4.5 not listed"
        if probe_chat and pr.get("has_grok_45"):
            init_delay = (
                DEFAULT_CHAT_PROBE_INITIAL_DELAY_SEC
                if probe_chat_initial_delay_sec is None
                else probe_chat_initial_delay_sec
            )
            delays = (
                DEFAULT_CHAT_PROBE_RETRY_DELAYS_SEC
                if probe_chat_retry_delays_sec is None
                else probe_chat_retry_delays_sec
            )
            log(
                "probe chat: start "
                f"(initial_delay_sec={init_delay}, "
                f"attempts={1 + len(tuple(delays))}, retry_delays_sec={list(delays)})"
            )
            ch = probe_mini_response_with_retry(
                tokens["access_token"],
                base_url=base_url,
                proxy=resolved or None,
                initial_delay_sec=init_delay,
                retry_delays_sec=delays,
                log=log,
                cancel=cancel,
            )
            result["probe_chat"] = ch
            log(
                f"probe chat: ok={ch.get('ok')} attempts={ch.get('attempts')} "
                f"model={ch.get('model')} text={ch.get('text')!r}"
            )
            if not ch.get("ok"):
                result["ok"] = False
                result["error"] = (
                    f"chat probe failed after {ch.get('attempts') or '?'} attempt(s): "
                    f"{ch.get('error') or ch.get('status')}"
                )
    return result
