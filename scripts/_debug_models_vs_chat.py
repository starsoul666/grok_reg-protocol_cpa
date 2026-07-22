#!/usr/bin/env python3
"""Diagnose: is /v1/models 403 a false negative while chat actually works?

Re-mint via PKCE for the failed account, then call BOTH /models and /responses.
If /models=403 but /responses=200, the /models gate in mint.py is a false killer.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1] if Path(__file__).resolve().parent.name == "scripts" else Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from cpa_xai import parse_accounts_file  # noqa: E402
from cpa_xai.pkce_mint import mint_with_sso_pkce  # noqa: E402
from cpa_xai.probe import probe_models, probe_mini_response  # noqa: E402
from cpa_xai.proxyutil import resolve_proxy, set_runtime_proxy  # noqa: E402

PROXY = "http://127.0.0.1:7890"
BASE_URL = "https://cli-chat-proxy.grok.com/v1"
TARGET_EMAIL = "owwxi5br@freeopenai.online"


def main() -> int:
    resolved = resolve_proxy(PROXY)
    set_runtime_proxy(resolved or None)

    accounts = parse_accounts_file(str(_ROOT / "accounts_cli.txt"))
    acc = next((a for a in accounts if a.email.lower() == TARGET_EMAIL.lower()), None)
    if not acc:
        print(f"[!] account {TARGET_EMAIL} not found in accounts_cli.txt")
        return 1
    if not acc.sso:
        print(f"[!] account {TARGET_EMAIL} has no sso (fields={acc._fields if hasattr(acc, '_fields') else '?'})")
        return 1

    print(f"[*] target={acc.email} sso_len={len(acc.sso)} proxy={PROXY}")

    print("[*] PKCE mint ...")
    tokens = mint_with_sso_pkce(
        sso_cookie=acc.sso,
        email=acc.email,
        proxy=resolved or None,
        log=lambda m: print(f"    {m}"),
    )
    at = tokens["access_token"]
    print(f"[*] access_token len={len(at)} refresh_token len={len(tokens.get('refresh_token','') or '')}")
    # decode jwt payload (no sig) for scopes/aud
    try:
        from cpa_xai.schema import jwt_payload
        pl = jwt_payload(at)
        print(f"[*] jwt payload keys={list(pl.keys())}")
        for k in ("iss", "aud", "sub", "exp", "iat", "scope", "scp", "client_id", "azp", "email"):
            if k in pl:
                print(f"    {k} = {pl[k]!r}")
    except Exception as e:
        print(f"[!] jwt decode failed: {e}")

    print("\n[*] probe /v1/models ...")
    pr = probe_models(at, base_url=BASE_URL, proxy=resolved or None)
    print(f"    models: ok={pr.get('ok')} status={pr.get('status')} "
          f"has_grok_45={pr.get('has_grok_45')} ids={pr.get('model_ids')} "
          f"error={str(pr.get('error') or '')[:300]}")

    print("\n[*] probe /v1/responses (chat) ...")
    ch = probe_mini_response(at, base_url=BASE_URL, timeout=90.0, proxy=resolved or None)
    print(f"    chat: ok={ch.get('ok')} status={ch.get('status')} "
          f"model={ch.get('model')} text={ch.get('text')!r}")
    print(f"    error={str(ch.get('error') or '')[:400]}")

    print("\n=== verdict ===")
    if not pr.get("ok") and ch.get("ok"):
        print("FALSE NEGATIVE: /models 403 but chat works -> mint.py /models gate is wrong")
    elif pr.get("ok") and ch.get("ok"):
        print("BOTH OK: token fine (flaky /models earlier?)")
    elif not pr.get("ok") and not ch.get("ok"):
        print("BOTH FAIL: token genuinely lacks permission / account not activated")
    else:
        print("models ok but chat fail -> see chat error")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
