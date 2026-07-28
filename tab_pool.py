#!/usr/bin/env python3
"""TabPool — per-thread Chromium with proper lifecycle.

Interface:
    TabPool.init(options_factory, browser_factory=...) → save factories (no browser yet)
    TabPool.get_tab()             → get/create current thread browser tab
    TabPool.clear_session()       → wipe cookies/storage; keep process warm
    TabPool.release_tab()         → quit current thread browser + drop registry
    TabPool.shutdown()            → quit all known browsers

Notes:
    - One Chromium per worker thread (cookie isolation).
    - Prefer clear_session() between accounts; release_tab() only on errors / GC.
    - _all_browsers is pruned on release to avoid zombie list growth.
    - browser_factory (optional) may return a Chromium already attached to BitBrowser;
      release then goes through bitbrowser.release_attached instead of quit(del_data=True).
"""

from __future__ import annotations

import threading
from typing import Any, Callable


class TabPool:
    """Per-thread Chromium instance manager."""

    _options_factory = None
    _browser_factory: Callable[[], Any] | None = None
    _tab_init_hook: Callable[[Any, Any], None] | None = None
    _options_lock = threading.Lock()
    _thread_local = threading.local()
    _all_browsers: list[Any] = []
    _all_browsers_lock = threading.Lock()

    # ── public ──

    @classmethod
    def init(cls, browser_options_or_factory, log_callback=None, browser_factory=None, tab_init_hook=None):
        """Save options object/factory and optional full browser factory.

        browser_factory: () -> Chromium
            When set, used instead of Chromium(options_factory()).
        tab_init_hook: (tab, browser) -> None
            Called whenever a new tab is assigned to a thread (get_tab / sync_tab /
            clear_session). Used by fingerprint backend to re-inject overrides.
        """
        with cls._options_lock:
            if browser_factory is not None and not callable(browser_factory):
                raise TypeError("browser_factory must be callable")
            cls._browser_factory = browser_factory
            if tab_init_hook is not None and not callable(tab_init_hook):
                raise TypeError("tab_init_hook must be callable")
            cls._tab_init_hook = tab_init_hook
            if browser_options_or_factory is None:
                cls._options_factory = None
            elif callable(browser_options_or_factory):
                cls._options_factory = browser_options_or_factory
            else:
                # Shared options object: auto_port will NOT re-allocate.
                cls._options_factory = lambda: browser_options_or_factory
        if log_callback:
            backend = "custom" if browser_factory else "chromium-options"
            extras = []
            if browser_factory:
                extras.append("browser_factory")
            if tab_init_hook:
                extras.append("tab_init_hook")
            extra = f"+{','.join(extras)}" if extras else ""
            log_callback(f"[*] TabPool 已初始化浏览器工厂 ({backend}{extra})")

    @classmethod
    def _create_browser(cls):
        from DrissionPage import Chromium

        with cls._options_lock:
            browser_factory = cls._browser_factory
            options_factory = cls._options_factory

        if browser_factory is not None:
            browser = browser_factory()
        else:
            if options_factory is None:
                return None
            options = options_factory()
            browser = Chromium(options)

        with cls._all_browsers_lock:
            cls._all_browsers.append(browser)
        return browser

    @classmethod
    def _unregister(cls, browser) -> None:
        if browser is None:
            return
        with cls._all_browsers_lock:
            try:
                cls._all_browsers = [b for b in cls._all_browsers if b is not browser]
            except Exception:
                pass

    @classmethod
    def _apply_tab_init_hook(cls, tab, browser) -> None:
        hook = cls._tab_init_hook
        if hook is None or tab is None:
            return
        try:
            hook(tab, browser)
        except Exception:
            pass

    @classmethod
    def _dispose_browser(cls, browser, log_callback=None) -> None:
        """Release one browser instance (BitBrowser / fingerprint aware)."""
        if browser is None:
            return
        if getattr(browser, "_bitbrowser_id", None) or getattr(browser, "_bitbrowser_meta", None):
            try:
                from bitbrowser import release_attached

                release_attached(browser, log=log_callback)
            except Exception:
                try:
                    browser.quit()
                except Exception:
                    pass
            return
        if getattr(browser, "_adspower_id", None) or getattr(browser, "_adspower_meta", None):
            try:
                from adspower import release_attached as adspower_release

                adspower_release(browser, log=log_callback)
            except Exception:
                try:
                    browser.quit()
                except Exception:
                    pass
            return
        if getattr(browser, "_fingerprint", None):
            try:
                from fingerprint import release_fingerprint_browser

                release_fingerprint_browser(browser, log=log_callback)
            except Exception:
                try:
                    browser.quit()
                except Exception:
                    pass
            return
        try:
            browser.quit(del_data=True)
        except TypeError:
            try:
                browser.quit()
            except Exception:
                pass
        except Exception:
            pass

    @classmethod
    def get_tab(cls, url=None):
        """Return current thread tab; create Chromium on first use."""
        tab = getattr(cls._thread_local, "tab", None)
        if tab is not None:
            return tab
        browser = cls._create_browser()
        if browser is None:
            raise RuntimeError("TabPool not initialized — call init() first")
        tab_ids = browser.tab_ids
        if tab_ids:
            tab = browser.get_tab(tab_ids[0])
        else:
            tab = browser.new_tab()
        cls._thread_local.browser = browser
        cls._thread_local.tab = tab
        cls._thread_local.served = 0
        cls._apply_tab_init_hook(tab, browser)
        return tab

    @classmethod
    def sync_tab(cls):
        """Point thread-local tab at the browser's latest tab."""
        browser = getattr(cls._thread_local, "browser", None)
        if browser is None:
            return
        tabs = browser.tab_ids
        if tabs:
            cls._thread_local.tab = browser.get_tab(tabs[-1])
            cls._apply_tab_init_hook(cls._thread_local.tab, browser)

    @classmethod
    def clear_session(cls, log_callback=None) -> bool:
        """Clear cookies/storage and blank the page; keep Chromium process.

        Returns True if session was cleared on a live browser; False if no browser.
        """
        browser = getattr(cls._thread_local, "browser", None)
        tab = getattr(cls._thread_local, "tab", None)
        if browser is None:
            return False
        ok = True
        try:
            if tab is not None:
                try:
                    tab.get("about:blank")
                except Exception:
                    pass
                for js in (
                    "try{localStorage.clear()}catch(e){}",
                    "try{sessionStorage.clear()}catch(e){}",
                    "try{indexedDB.databases&&indexedDB.databases().then(ds=>ds.forEach(d=>indexedDB.deleteDatabase(d.name)))}catch(e){}",
                ):
                    try:
                        tab.run_js(js)
                    except Exception:
                        pass
            # Best-effort cookie wipe (API varies by DrissionPage version)
            cleared = False
            for target in (tab, browser):
                if target is None or cleared:
                    continue
                for attr_path in (
                    ("set", "cookies", "clear"),
                    ("cookies", "clear"),
                ):
                    try:
                        obj = target
                        for name in attr_path[:-1]:
                            obj = getattr(obj, name)
                        fn = getattr(obj, attr_path[-1])
                        fn()
                        cleared = True
                        break
                    except Exception:
                        continue
            if not cleared:
                try:
                    # Fallback: drop all cookies via CDP-ish helper if present
                    cks = browser.cookies()
                    if isinstance(cks, list):
                        for c in cks:
                            try:
                                browser.set.cookies.remove(c)  # type: ignore[attr-defined]
                            except Exception:
                                pass
                except Exception:
                    ok = False
            # Prefer a single clean tab
            try:
                tabs = list(browser.tab_ids or [])
                if len(tabs) > 1:
                    keep = tabs[0]
                    for tid in tabs[1:]:
                        try:
                            browser.get_tab(tid).close()
                        except Exception:
                            pass
                    cls._thread_local.tab = browser.get_tab(keep)
                    cls._apply_tab_init_hook(cls._thread_local.tab, browser)
                elif tabs:
                    cls._thread_local.tab = browser.get_tab(tabs[0])
                    cls._apply_tab_init_hook(cls._thread_local.tab, browser)
            except Exception:
                cls.sync_tab()
            if log_callback:
                served = int(getattr(cls._thread_local, "served", 0) or 0)
                log_callback(f"[*] 浏览器会话已清理（复用进程, served={served}）")
            return ok
        except Exception as exc:
            if log_callback:
                log_callback(f"[!] clear_session 失败: {exc}")
            return False

    @classmethod
    def mark_served(cls) -> int:
        n = int(getattr(cls._thread_local, "served", 0) or 0) + 1
        cls._thread_local.served = n
        return n

    @classmethod
    def served_count(cls) -> int:
        return int(getattr(cls._thread_local, "served", 0) or 0)

    @classmethod
    def release_tab(cls, log_callback=None):
        """Quit current thread Chromium and unregister it."""
        browser = getattr(cls._thread_local, "browser", None)
        if browser is not None:
            cls._dispose_browser(browser, log_callback=log_callback)
            cls._unregister(browser)
        cls._thread_local.browser = None
        cls._thread_local.tab = None
        cls._thread_local.served = 0

    @classmethod
    def refresh_tab(cls):
        """Full recycle: quit + new browser."""
        cls.release_tab()
        return cls.get_tab()

    @classmethod
    def shutdown(cls, log_callback=None):
        """Quit every browser we still track."""
        cls.release_tab(log_callback=log_callback)
        with cls._all_browsers_lock:
            browsers = list(cls._all_browsers)
            cls._all_browsers.clear()
        for b in browsers:
            cls._dispose_browser(b, log_callback=log_callback)

    @classmethod
    def live_count(cls) -> int:
        with cls._all_browsers_lock:
            return len(cls._all_browsers)

    @classmethod
    def get_browser(cls):
        return getattr(cls._thread_local, "browser", None)
