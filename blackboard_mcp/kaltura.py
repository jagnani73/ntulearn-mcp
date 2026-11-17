"""Lecture recordings from a course's Media Gallery.

NTU's Media Gallery is Kaltura MediaSpace (KAF) on its own host, ntulearnv1.ntu.edu.sg, opened from the course
through an LTI 1.3 launch. That host can't be visited directly, and Kaltura's API won't list a channel's entries for
a gallery session, so this replays the launch in the saved (headless) browser profile, reads the lecture IDs off the
gallery page, and takes a Kaltura session (KS) from a lecture page. Everything after that is plain Kaltura API calls.

The Kaltura behaviour was worked out in ntulearn-media-gallery-downloader (the Chrome extension); see its CLAUDE.md.
"""
from __future__ import annotations

import base64
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from .auth import PROFILE_LOCK, LoginRequired, _launch, load_cookies
from .config import Settings

SERVICE_URL = "https://api.sg.kaltura.com"
# A v2 KS is base64 of "v2|<partnerId>|…", so it always starts "djJ8". A KAF page embeds several and they aren't
# interchangeable: some are widget sessions capped by an actions limit, some are pinned to one URI. Only probing
# tells them apart; on NTU the lecture page carries one full user session and the gallery page carries none.
KS_RE = re.compile(r"djJ8[A-Za-z0-9_\-+/=]{60,}")
ENTRY_PATTERNS = (re.compile(r"/thumbnail/entry_id/([01]_[a-z0-9]{8})"),
                  re.compile(r"/media/(?:t/)?(?:[^/\"'?#\s]+/)?([01]_[a-z0-9]{8})"),
                  re.compile(r"data-entry-?id=[\"']([01]_[a-z0-9]{8})"))
SERVICE_URL_RE = re.compile(r"\"serviceUrl\"\s*:\s*\"(https://[^\"]+)\"")
GALLERY_PATH = re.compile(r"/(channel|category)/|gallery", re.I)
SESSION_ERRORS = {"INVALID_KS", "KS_EXPIRED", "EXPIRED_KS", "INVALID_ACTIONS_LIMIT", "SERVICE_FORBIDDEN"}
CUE_TIME = re.compile(r"(\d+):(\d{2}):(\d{2})[,.]\d{1,3}\s*-->")


class KalturaError(RuntimeError):
    def __init__(self, code: str | None, message: str | None):
        super().__init__(f"Kaltura {code}: {message}")
        self.code = code or ""

    @property
    def session_problem(self) -> bool:
        return self.code in SESSION_ERRORS


class Kaltura:
    """Kaltura api_v3 client for one session."""

    def __init__(self, ks: str, service_url: str = SERVICE_URL):
        self.ks = ks
        self.service_url = service_url.rstrip("/")
        self._http = httpx.Client(timeout=httpx.Timeout(30, read=90), follow_redirects=True)

    def call(self, service: str, action: str, **params) -> object:
        r = self._http.post(f"{self.service_url}/api_v3/service/{service}/action/{action}",
                            data={"format": "1", "ks": self.ks, "clientTag": "ntulearn-mcp", **params})
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and data.get("objectType") == "KalturaAPIException":
            raise KalturaError(data.get("code"), data.get("message"))
        return data

    def _list(self, service: str, filter_type: str, **filt) -> list[dict]:
        params = {"filter:objectType": filter_type, "pager:pageSize": "500"}
        params.update({f"filter:{k}": v for k, v in filt.items()})
        return self.call(service, "list", **params).get("objects") or []

    def entries(self, ids: list[str]) -> list[dict]:
        return self._list("baseentry", "KalturaBaseEntryFilter", idIn=",".join(ids)) if ids else []

    def second_streams(self, ids: list[str]) -> dict[str, str]:
        """{parent entry id: child entry id} for dual-stream recordings (camera + screen are two entries)."""
        kids = self._list("baseentry", "KalturaBaseEntryFilter", parentEntryIdIn=",".join(ids)) if ids else []
        return {k["parentEntryId"]: k["id"] for k in kids if k.get("parentEntryId")}

    def captions(self, entry_ids: list[str]) -> dict[str, list[dict]]:
        """{entry id: [ready caption assets]}. Captions live on the parent entry and cover both streams."""
        out: dict[str, list[dict]] = {}
        if entry_ids:
            for cap in self._list("caption_captionasset", "KalturaAssetFilter", entryIdIn=",".join(entry_ids)):
                if cap.get("status") == 2:
                    out.setdefault(cap["entryId"], []).append(cap)
        return out

    def caption_file(self, asset_id: str) -> str:
        url = self.call("caption_captionasset", "getUrl", id=asset_id)  # a plain CDN URL, no token needed
        r = self._http.get(str(url))
        r.raise_for_status()
        return r.text


@dataclass
class Gallery:
    """One course's Media Gallery, as seen through the LTI launch."""
    entry_ids: list[str]
    ks: str | None            # None when the gallery is empty (nothing to take a session from)
    service_url: str = SERVICE_URL
    url: str = ""
    opened: float = field(default_factory=time.time)

    def client(self) -> Kaltura:
        if not self.ks:
            raise ValueError("This Media Gallery has no recordings yet.")
        return Kaltura(self.ks, self.service_url)


def open_gallery(settings: Settings, launch_path: str, kaf_host: str, timeout_s: int = 75) -> Gallery:
    """Run the course's Media Gallery LTI launch in the saved browser profile (headless) and collect what the
    Kaltura API needs: the lecture entry IDs on the gallery page and a usable user session."""
    from playwright.sync_api import sync_playwright

    with PROFILE_LOCK, sync_playwright() as pw:
        ctx = _launch(pw, settings, headless=True)
        try:
            # The persistent profile may have dropped Blackboard's session cookies; the saved ones are current.
            keys = ("name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite")
            ctx.add_cookies([{k: c[k] for k in keys if k in c} for c in load_cookies(settings)])
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(settings.base_url + launch_path, wait_until="domcontentloaded")
            frame = _wait_for_gallery(page, settings, kaf_host, timeout_s)
            ids = _gallery_entry_ids(frame)
            origin = "{0.scheme}://{0.netloc}".format(urlsplit(frame.url))
            ks, service_url = _user_session(ctx, origin, ids)
            return Gallery(entry_ids=ids, ks=ks, service_url=service_url, url=frame.url)
        finally:
            ctx.close()


def _wait_for_gallery(page, settings: Settings, kaf_host: str, timeout_s: int):
    bb_host = urlsplit(settings.base_url).hostname or ""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for f in page.frames:
            u = urlsplit(f.url)
            if u.hostname == kaf_host and GALLERY_PATH.search(u.path):
                try:
                    f.wait_for_load_state("networkidle", timeout=20000)
                except Exception:  # noqa: BLE001 - a busy page still has its tiles
                    pass
                return f
        top = urlsplit(page.url).hostname
        if top and (top not in (bb_host, kaf_host) or "/login" in page.url.lower()):
            raise LoginRequired()  # bounced to Blackboard's or Microsoft's sign-in page
        page.wait_for_timeout(1000)
    raise ValueError("The Media Gallery didn't open (the LTI launch never reached Kaltura). Try again later.")


def _gallery_entry_ids(frame) -> list[str]:
    """Lecture IDs on the gallery, in page order. Scrolls and presses "load more" until no new tiles appear."""
    seen: dict[str, None] = {}
    idle = 0
    for _ in range(20):
        html = frame.content()
        before = len(seen)
        for pat in ENTRY_PATTERNS:
            seen.update(dict.fromkeys(pat.findall(html)))
        idle = idle + 1 if len(seen) == before else 0
        if idle >= 2:  # two rounds of scrolling with nothing new: that's every tile
            break
        try:
            frame.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            more = frame.get_by_text(re.compile(r"^\s*(load|show) more", re.I))
            if more.count():
                more.first.click(timeout=3000)
        except Exception:  # noqa: BLE001 - no button, or it went away
            pass
        frame.wait_for_timeout(1500)
    return list(seen)


def _user_session(ctx, origin: str, ids: list[str]) -> tuple[str | None, str]:
    """A KS the API accepts, taken from a lecture page's player config (the gallery's own are capped)."""
    for entry_id in ids[:3]:
        r = ctx.request.get(f"{origin}/media/t/{entry_id}")
        if not r.ok:
            continue
        html = r.text()
        m = SERVICE_URL_RE.search(html)
        service_url = m.group(1).replace("\\/", "/") if m else SERVICE_URL
        if "analytics." in service_url:  # the player's beacon host answers on /api_v3/ but isn't the API
            service_url = SERVICE_URL
        for ks in dict.fromkeys(KS_RE.findall(html)):
            if partner_from_ks(ks) is None:
                continue
            try:
                Kaltura(ks, service_url).entries([entry_id])
                return ks, service_url
            except KalturaError:
                continue
    if ids:
        raise ValueError("Couldn't get a working Kaltura session from the Media Gallery.")
    return None, SERVICE_URL


def partner_from_ks(ks: str) -> int | None:
    try:
        m = re.match(rb"v2\|(\d+)\|", base64.urlsafe_b64decode(ks[:24] + "=="))
    except ValueError:
        return None
    return int(m.group(1)) if m else None


def captions_to_text(raw: str, paragraph_s: int = 60) -> str:
    """SRT or WebVTT to readable paragraphs, each stamped with the time it starts at: "[0:12:30] …"."""
    cues: list[tuple[int, str]] = []
    for block in re.split(r"\n\s*\n", raw.replace("\r\n", "\n").strip()):
        lines = block.split("\n")
        for i, line in enumerate(lines):
            m = CUE_TIME.search(line)
            if m:
                h, mi, s = map(int, m.groups())
                text = re.sub(r"<[^>]+>", "", " ".join(x.strip() for x in lines[i + 1:] if x.strip()))
                if text and (not cues or cues[-1][1] != text):
                    cues.append((h * 3600 + mi * 60 + s, text))
                break
    paras: list[tuple[int, list[str]]] = []
    for t, text in cues:
        if not paras or t - paras[-1][0] >= paragraph_s:
            paras.append((t, []))
        paras[-1][1].append(text)
    return "\n\n".join(f"[{hms(t)}] {' '.join(words)}" for t, words in paras)


def hms(seconds: int | float | None) -> str:
    s = int(seconds or 0)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"
