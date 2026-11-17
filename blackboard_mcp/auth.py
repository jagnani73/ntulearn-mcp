"""Signing in to Blackboard.

Most universities put Blackboard behind single sign-on with MFA, so there is
no password we can post from a script. Instead we open a real browser window
(your installed Chrome, with its own dedicated profile), you sign in once, and we
keep the resulting session cookies. The same browser profile remembers the SSO
"stay signed in" state, so later refreshes can usually happen invisibly.
"""
from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .config import Settings

_profile_thread_lock = threading.RLock()
_profile_depth = 0
_profile_file = None


@contextmanager
def profile_lock(settings: Settings, wait_s: float = 120):
    """Hold the sign-in browser profile. Chrome allows one process per profile directory, and every MCP client
    (Claude Code, Cursor, Claude Desktop…) starts its own server process, so this is a lock file in BB_MCP_HOME
    that holds across processes, not only threads. The OS drops it if a process dies. Re-entrant per process."""
    global _profile_depth, _profile_file
    with _profile_thread_lock:
        if _profile_depth == 0:
            _profile_file = _lock_file(settings.home / "browser.lock", wait_s)
        _profile_depth += 1
        try:
            yield
        finally:
            _profile_depth -= 1
            if _profile_depth == 0:
                _unlock_file(_profile_file)
                _profile_file = None


def _lock_file(path: Path, wait_s: float):
    fh = open(path, "a+b")
    deadline = time.time() + wait_s
    while True:
        try:
            if os.name == "nt":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fh
        except OSError:
            if time.time() > deadline:
                fh.close()
                raise TimeoutError("Another app is using the NTULearn sign-in browser right now. Try again in a "
                                   "minute.") from None
            time.sleep(0.5)


def _unlock_file(fh) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_UN)
    finally:
        fh.close()

SESSION_COOKIES = ("BbRouter", "JSESSIONID", "AWSELB", "AWSELBCORS", "BbClientCalenderTimeZone")
# Some Blackboard landing pages don't redirect to single sign-on by themselves; one of these buttons starts it.
# (Bristol: "Staff and student login".) Clicking it lets the silent refresh work with no window.
SSO_BUTTONS = ("Staff and student login", "Student and staff login", "Student login", "Single sign-on",
               "Single Sign On", "Institutional login", "University login", "Sign in with", "Log in with SSO")
# Headless Chrome advertises "HeadlessChrome", which identity providers may treat differently.
DESKTOP_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/154.0.0.0 Safari/537.36")


class LoginRequired(RuntimeError):
    """Raised when there is no usable Blackboard session."""

    def __init__(self, msg: str = ""):
        super().__init__(
            msg
            or "Not signed in to Blackboard (or the session expired). "
            "Run the `bb_login` tool (or `blackboard-mcp login` in a terminal) "
            "and complete the university sign-in in the window that opens."
        )


def load_cookies(settings: Settings) -> list[dict]:
    p = settings.cookies_path
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError:
        return []
    # Whole cookies, not name -> value: the domain keeps them off Blackboard's alternate file host
    # (downloads redirect there), and the path keeps same-named cookies apart (NTU sets two JSESSIONIDs).
    return data.get("cookies", [])


def save_cookies(settings: Settings, cookies: list[dict]) -> None:
    p = settings.cookies_path
    p.write_text(json.dumps({"saved": time.time(), "cookies": cookies}, indent=1))
    p.chmod(0o600)


def _host(settings: Settings) -> str:
    from urllib.parse import urlparse
    return urlparse(settings.base_url).hostname or ""


def _launch(pw, settings: Settings, headless: bool):
    settings.profile_dir.mkdir(parents=True, exist_ok=True)
    kwargs = dict(user_data_dir=str(settings.profile_dir), headless=headless,
                  viewport={"width": 1100, "height": 800})
    if headless:
        kwargs["user_agent"] = DESKTOP_UA
    if settings.browser_channel:
        try:
            return pw.chromium.launch_persistent_context(channel=settings.browser_channel, **kwargs)
        except Exception:
            pass  # Chrome not installed -> fall back to Playwright's Chromium
    return pw.chromium.launch_persistent_context(**kwargs)


def browser_login(settings: Settings, *, headless: bool = False, timeout_s: int = 300) -> dict:
    """Open Blackboard in a browser and wait until we land in Ultra, signed in.

    headless=True is the silent refresh: it only succeeds if the SSO session in
    the saved browser profile is still valid.
    """
    from playwright.sync_api import sync_playwright

    if not settings.base_url:
        return {"ok": False, "reason": "Set your Blackboard address first (BB_BASE_URL, e.g. https://learn.example.ac.uk)."}
    host = _host(settings)
    deadline = time.time() + (45 if headless else timeout_s)
    with profile_lock(settings, wait_s=timeout_s), sync_playwright() as pw:
        ctx = _launch(pw, settings, headless)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(settings.base_url + "/ultra/", wait_until="domcontentloaded")
            if not headless:
                try:
                    page.bring_to_front()
                except Exception:
                    pass
            clicked_sso = False
            while time.time() < deadline:
                url = page.url
                on_bb = host in url and "/ultra" in url and "login" not in url.lower()
                if not clicked_sso and host in url and "/ultra" not in url:
                    # On the landing page: start SSO. With a valid "stay signed in" cookie in the
                    # profile, Microsoft signs us straight back in without MFA.
                    for text in ((settings.sso_button,) if settings.sso_button else ()) + SSO_BUTTONS:
                        try:
                            btn = page.get_by_text(text, exact=False)
                            if btn.count():
                                btn.first.click()
                                clicked_sso = True
                                break
                        except Exception:
                            pass
                cookies = [c for c in ctx.cookies() if host.endswith(c["domain"].lstrip("."))]
                names = {c["name"] for c in cookies}
                if on_bb and "BbRouter" in names:
                    # Confirm with the API from inside the browser session.
                    try:
                        resp = page.request.get(settings.base_url + "/learn/api/public/v1/users/me")
                        if resp.ok:
                            me = resp.json()
                            save_cookies(settings, cookies)
                            return {"ok": True, "user": me.get("userName"),
                                    "name": (me.get("name") or {}).get("given")}
                    except Exception:
                        pass
                page.wait_for_timeout(1000)
            return {"ok": False, "reason": "timed out waiting for sign-in"}
        finally:
            ctx.close()


def refresh_silently(settings: Settings) -> bool:
    try:
        return bool(browser_login(settings, headless=True).get("ok"))
    except Exception:
        return False
