#!/usr/bin/env python3
"""Diagnose: do PREVIOUSLY-SUCCESSFUL accounts also 403 now?

Read an existing cpa_auths/xai-*.json, refresh its access_token via refresh_token,
then probe /models + /responses. If old good accounts also 403 now -> upstream
policy changed (all blocked). If old accounts still work -> only new accounts
lack activation.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1] if Path(__file__).resolve().parent.name == "scripts" else Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from cpa_xai.probe import probe_models, probe_mini_response  # noqa: E402
from cpa_xai.proxyutil import resolve_proxy, set_runtime_proxy  # noqa: E402
from cpa_xai.schema import DEFAULT_CLIENT_HEADERS, DEFAULT_TOKEN_ENDPOINT, jwt_payload  # noqa: E402

PROXY = "http://127.0.0.1:7890"
BASE_URL = "https://cli-chat-proxy.grok.com/v1"
CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"


def refresh_access_token(refresh_token: str, proxy: str | None) -> dict:
    try:
        from curl_cffi import requests as cf_requests
    except ImportError as e:
        return {"ok": False, "status": 0, "error": f"curl_cffi not installed: {e}"}

    resolved = resolve_proxy(proxy)
    kwargs: dict = {"impersonate": "chrome131"}
    if resolved:
        kwargs["proxies"] = {"http": resolved, "https": resolved}
    session = cf_requests.Session(**kwargs)
    resp = session.post(
        DEFAULT_TOKEN_ENDPOINT,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLIENT_ID,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=45,
    )
    if resp.status_code != 200:
        return {"ok": False, "status": resp.status_code, "error": resp.text[:400]}
    return {"ok": True, "body": resp.json()}


def main() -> int:
    resolved = resolve_proxy(PROXY)
    set_runtime_proxy(resolved or None)

    auth_dir = _ROOT / "cpa_auths"
    files = sorted(auth_dir.glob("xai-*.json"))
    if not files:
        print("[!] no xai-*.json in cpa_auths")
        return 1

    # test up to 3 old accounts
    sample = files[:3]
    for fp in sample:
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[!] {fp.name} parse fail: {e}")
            continue
        rt = (data.get("refresh_token") or "").strip()
        email = data.get("email") or fp.stem
        if not rt:
            print(f"[-] {fp.name} ({email}): no refresh_token, skip")
            continue
        print(f"\n=== {fp.name} (email={email}) ===")
        r = refresh_access_token(rt, resolved or None)
        if not r.get("ok"):
            print(f"    refresh FAILED status={r.get('status')} err={r.get('error')}")
            continue
        at = (r["body"].get("access_token") or "").strip()
        if not at:
            print(f"    refresh ok but no access_token: {r['body']}")
            continue
        try:
            pl = jwt_payload(at)
            print(f"    scope={pl.get('scope')!r}")
        except Exception:
            pass
        pr = probe_models(at, base_url=BASE_URL, proxy=resolved or None)
        print(f"    models: ok={pr.get('ok')} status={pr.get('status')} "
              f"has_grok_45={pr.get('has_grok_45')} ids={pr.get('model_ids')} "
              f"err={str(pr.get('error') or '')[:200]}")
        ch = probe_mini_response(at, base_url=BASE_URL, timeout=60.0, proxy=resolved or None)
        print(f"    chat: ok={ch.get('ok')} status={ch.get('status')} "
              f"text={ch.get('text')!r} err={str(ch.get('error') or '')[:200]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
