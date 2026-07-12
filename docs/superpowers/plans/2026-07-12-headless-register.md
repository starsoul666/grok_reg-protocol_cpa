# Headless Register Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add optional headless mode for the registration Chromium while keeping headed mode as the default.

**Architecture:** Reuse the existing `grok_register_ttk.py:create_browser_options()` factory as the single source of browser option behavior. `register_cli.py` only resolves CLI overrides into `reg.config` before `TabPool.init()`. Diagnostics stay inside the existing Turnstile helper so both CLI and GUI benefit from the same failure artifacts.

**Tech Stack:** Python 3.13, DrissionPage `ChromiumOptions`, existing JSON config and argparse CLI.

---

## File Structure

- Modify `grok_register_ttk.py`
  - Add `DEFAULT_CONFIG["register_headless"] = False`.
  - Add helper functions for boolean config parsing and headless browser option application.
  - Call the helper from `create_browser_options()`.
  - Add best-effort Turnstile failure diagnostics.
- Modify `register_cli.py`
  - Add `--headless-register` and `--headed-register`.
  - Resolve CLI override after `reg.load_config()` and before `TabPool.init()`.
  - Include register browser mode in startup logs.
- Modify `config.example.json`
  - Document `register_headless`.
- Modify `README.md`
  - Add headless register usage and risk note.
- Modify `CLAUDE.md`
  - Add a common command for headless registration.

## Task 1: Registration Browser Config and Headless Options

**Files:**
- Modify: `grok_register_ttk.py`

- [ ] **Step 1: Add config default**

Edit `DEFAULT_CONFIG` near the existing browser/network values:

```python
    "proxy": "http://127.0.0.1:7890",
    "register_headless": False,
    "enable_nsfw": True,
```

- [ ] **Step 2: Add helper functions before `create_browser_options()`**

Insert these helpers immediately above `def create_browser_options():`

```python
def _config_bool(name: str, default: bool = False) -> bool:
    value = config.get(name, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(default)


def _apply_register_headless_options(options) -> bool:
    """Apply optional headless mode for the registration browser."""
    headless = _config_bool("register_headless", False)
    if not headless:
        try:
            options.headless(False)
        except Exception:
            pass
        return False

    try:
        options.headless(True)
    except Exception:
        options.set_argument("--headless=new")
    options.set_argument("--window-size=1280,900")
    print("  [browser] register_headless=true")
    return True
```

- [ ] **Step 3: Call helper from `create_browser_options()`**

Change `create_browser_options()` so headless mode is applied after the slim flags and before extension/proxy setup:

```python
def create_browser_options():
    options = ChromiumOptions()
    options.auto_port()
    options.set_timeouts(base=1)
    for flag in CHROMIUM_SLIM_FLAGS:
        options.set_argument(flag)
    _apply_register_headless_options(options)
    if os.path.exists(EXTENSION_PATH):
        options.add_extension(EXTENSION_PATH)
    # Apply config.json "proxy" to Chromium. Without this, only HTTP helpers
    # used get_proxies(); the browser itself fell through to system/env proxy.
```

- [ ] **Step 4: Run syntax check**

Run:

```bash
uv run python -m py_compile grok_register_ttk.py
```

Expected: command exits 0 with no output.

- [ ] **Step 5: Commit**

```bash
git add grok_register_ttk.py
git commit -m "feat: add configurable register headless mode"
```

## Task 2: CLI Overrides for Register Browser Mode

**Files:**
- Modify: `register_cli.py`

- [ ] **Step 1: Add argparse flags**

Add these arguments after `--inline-mint`:

```python
    parser.add_argument(
        "--headless-register",
        action="store_true",
        help="注册浏览器使用无头模式（默认由 config.register_headless 决定）",
    )
    parser.add_argument(
        "--headed-register",
        action="store_true",
        help="注册浏览器强制有头模式，覆盖 config.register_headless",
    )
```

- [ ] **Step 2: Resolve CLI override after config load**

After:

```python
    reg.load_config()
    cfg0 = getattr(reg, "config", {}) or {}
```

insert:

```python
    if args.headless_register and args.headed_register:
        print("[!] --headless-register 与 --headed-register 不能同时使用", flush=True)
        return 2
    if args.headless_register:
        cfg0["register_headless"] = True
    elif args.headed_register:
        cfg0["register_headless"] = False
```

- [ ] **Step 3: Add mode to startup logs**

Update each of the three startup `print()` messages to include:

```python
f"register_headless={bool(cfg0.get('register_headless', False))}"
```

For example, the `--extra` message should become:

```python
        print(
            f"[*] 配置加载完成，额外新注册 {args.extra} 个（当前已有 {done_count} → 目标 {target_total}），"
            f"注册线程={threads} mint_workers={mint_workers} mint_queue_max={mint_qmax} "
            f"fast={fast} register_headless={bool(cfg0.get('register_headless', False))}",
            flush=True,
        )
```

Apply the same pattern to the unlimited and fixed-count branches.

- [ ] **Step 4: Run CLI help check**

Run:

```bash
uv run python -u register_cli.py --help
```

Expected: output contains both `--headless-register` and `--headed-register`.

- [ ] **Step 5: Verify conflicting flags fail**

Run:

```bash
uv run python -u register_cli.py --headless-register --headed-register
```

Expected: exits 2 and prints:

```text
[!] --headless-register 与 --headed-register 不能同时使用
```

- [ ] **Step 6: Commit**

```bash
git add register_cli.py
git commit -m "feat: add register headless CLI overrides"
```

## Task 3: Turnstile Failure Diagnostics

**Files:**
- Modify: `grok_register_ttk.py`

- [ ] **Step 1: Add diagnostic helper near `take_screenshot()`**

Insert this helper immediately after `take_screenshot()`:

```python
def save_turnstile_debug(page, tag: str = "turnstile_failed"):
    """Best-effort debug bundle for Turnstile failures."""
    if PERF_FLAGS.get("skip_debug_io"):
        return
    try:
        os.makedirs(_SCREENSHOT_DIR, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        base = os.path.join(_SCREENSHOT_DIR, f"{ts}_{tag}")
        try:
            page.get_screenshot(path=f"{base}.png")
            print(f"  [turnstile-debug] screenshot: {base}.png")
        except Exception as exc:
            print(f"  [turnstile-debug] screenshot err: {exc}")
        try:
            info = page.run_js("""() => ({
                url: location.href,
                title: document.title,
                text: (document.body && document.body.innerText || '').slice(0, 2000)
            })""")
            with open(f"{base}.txt", "w", encoding="utf-8") as f:
                json.dump(info or {}, f, ensure_ascii=False, indent=2)
            print(f"  [turnstile-debug] state: {base}.txt")
        except Exception as exc:
            print(f"  [turnstile-debug] state err: {exc}")
    except Exception as exc:
        print(f"  [turnstile-debug] err: {exc}")
```

- [ ] **Step 2: Call diagnostic helper before Turnstile failure**

At the end of `getTurnstileToken()`, replace:

```python
    raise Exception("Turnstile 获取 token 失败")
```

with:

```python
    save_turnstile_debug(page)
    raise Exception("Turnstile 获取 token 失败")
```

- [ ] **Step 3: Run syntax check**

Run:

```bash
uv run python -m py_compile grok_register_ttk.py
```

Expected: command exits 0 with no output.

- [ ] **Step 4: Commit**

```bash
git add grok_register_ttk.py
git commit -m "feat: add turnstile failure diagnostics"
```

## Task 4: Documentation Updates

**Files:**
- Modify: `config.example.json`
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update example config**

In `config.example.json`, under `网络 / 浏览器`, add:

```json
  "// register_headless": "注册浏览器是否无头。默认 false；Turnstile 成功率不稳定时改回 false",
  "register_headless": false,
```

Place it after `proxy`.

- [ ] **Step 2: Update README config table**

In `README.md`, add `register_headless` to the browser/network config description:

```markdown
| `register_headless` | 注册 Chromium 是否无头；默认 `false`，Turnstile 不稳定时保持有头 |
```

- [ ] **Step 3: Add README CLI usage**

Add this example near the registration commands:

```markdown
# 无头注册 1 个账号（默认仍是有头；Turnstile 失败时改回有头）
uv run python -u register_cli.py --extra 1 --threads 1 --headless-register

# 强制有头覆盖 config.register_headless
uv run python -u register_cli.py --extra 1 --threads 1 --headed-register
```

- [ ] **Step 4: Update CLAUDE common commands**

Add under “注册新账号”:

```bash
# 无头注册（默认仍建议有头，Turnstile 失败时去掉该参数）
uv run python -u register_cli.py --extra 1 --threads 1 --headless-register
```

- [ ] **Step 5: Run markdown/config sanity checks**

Run:

```bash
uv run python -m json.tool config.example.json >/tmp/config.example.json.checked
rg -n "register_headless|headless-register|headed-register" README.md CLAUDE.md config.example.json
```

Expected:

- `json.tool` exits 0.
- `rg` shows entries in all three files.

- [ ] **Step 6: Commit**

```bash
git add config.example.json README.md CLAUDE.md
git commit -m "docs: document register headless mode"
```

## Task 5: Final Verification

**Files:**
- Verify only.

- [ ] **Step 1: Run Python compile checks**

Run:

```bash
uv run python -m py_compile grok_register_ttk.py register_cli.py
```

Expected: command exits 0 with no output.

- [ ] **Step 2: Run CLI help check**

Run:

```bash
uv run python -u register_cli.py --help
```

Expected: help output contains:

```text
--headless-register
--headed-register
```

- [ ] **Step 3: Run config JSON check**

Run:

```bash
uv run python -m json.tool config.example.json >/tmp/config.example.json.checked
```

Expected: command exits 0.

- [ ] **Step 4: Inspect git diff/status**

Run:

```bash
git status --short
git log --oneline -5
```

Expected:

- Working tree is clean, unless runtime verification generated ignored artifacts.
- Recent commits include the feature and documentation commits.

## Self-Review

- Spec coverage: config option, CLI overrides, browser option application, Turnstile diagnostics, documentation, and verification are each mapped to tasks.
- Placeholder scan: no deferred placeholder text or unspecified implementation steps remain.
- Type consistency: config key is consistently `register_headless`; CLI args are consistently `headless_register` and `headed_register`; helper names are consistently `_config_bool`, `_apply_register_headless_options`, and `save_turnstile_debug`.
