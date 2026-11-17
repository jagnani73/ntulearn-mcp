# AGENTS.md

Notes for coding agents working in this repository. Claude Code reads this file through `CLAUDE.md`. The
NTU-specific behaviour below was checked against the live NTULearn on 2026-11-17, and it explains most of the
fork's changes.

## Rules

- Never print, log or commit anything from `.bb-home/` (`cookies.json` is a live NTULearn session) or a Kaltura
  session token.
- The server is read-only. Don't add anything that submits, posts or changes data on NTULearn or Kaltura.
- Links in course content are untrusted. Every Blackboard request starts on the NTULearn host
  (`Blackboard._request` refuses others), and only `/bbcswebdav/` links on that host count as files. Keep both.
- Don't write tests unless asked. Check a change by running the tools against the live NTULearn (see "Running and
  checking changes").
- Work on the `ntulearn` branch. `main` mirrors upstream.
- Conventional Commits: subject 50 characters or fewer (72 is the hard ceiling), imperative mood, no trailing
  period, one logical change per commit.
- No AI co-author or attribution trailers in commits or PR descriptions.
- Keep doc prose plain and short, with no em dashes.
- Keep this file and `README.md` current when behaviour or NTU findings change.

## What this is

A read-only MCP server for NTULearn (`https://ntulearn.ntu.edu.sg`, Blackboard Learn Ultra), forked from
[Mattlo0546/blackboard-mcp](https://github.com/Mattlo0546/blackboard-mcp) (git remote `upstream`). Package,
CLI and MCP server names are still upstream's (`blackboard_mcp`, `blackboard-mcp`, tools `bb_*`), which keeps
upstream fixes easy to cherry-pick. Renaming is cheap if that stops mattering.

Why fork instead of using an existing server: upstream's Playwright SSO login works on NTU and on Windows. The
NTU-specific alternative, kingdonkatsu/ntulearn-mcp, reads the `BbRouter` cookie out of the browser with
`browser-cookie3`, and Chrome's App-Bound Encryption blocks that on Windows. Neither server handled NTU's
pptx-heavy content, and neither covers lecture recordings.

## Layout

| File | Job |
| --- | --- |
| `blackboard_mcp/auth.py` | Playwright sign-in (interactive, plus headless silent refresh) and the saved cookies |
| `blackboard_mcp/bb.py` | REST client over the saved session: courses, content, files, gradebook, calendar, announcements |
| `blackboard_mcp/extract.py` | Text out of pptx/pdf/docx/xlsx/text bytes. Adapted from ntulearn-mcp; its MIT notice is in `LICENSE` |
| `blackboard_mcp/kaltura.py` | Media Gallery: headless LTI launch, entry IDs off the gallery page, KS probing, Kaltura API, captions to text |
| `blackboard_mcp/sync.py` | Incremental course sync and file/folder naming (`safe`, `parse_week`, `STUDY_EXTS`) |
| `blackboard_mcp/relevance.py` | Which sites are real courses and which files are for studying (upstream, unchanged) |
| `blackboard_mcp/server.py` | FastMCP tools. Each wraps blocking client code in `_run`, which turns errors into `{"error": …}` |
| `blackboard_mcp/cli.py` | `login`, `status`, `courses`, `sync`, `serve` |

Local, gitignored:

- `.bb-home/`: `BB_MCP_HOME`. `cookies.json` is a live NTULearn session in plain text; never print or commit it.
- `downloads/`: `BB_DOWNLOAD_DIR` used during development.
- `.reference/ntulearn_mcp-0.2.0/`: kingdonkatsu/ntulearn-mcp sdist, kept for porting more of it.

## NTULearn facts (verified live)

- Interactive Playwright login through NTU's SSO and MFA works with no `BB_SSO_BUTTON`. The session is host-only
  cookies on `ntulearn.ntu.edu.sg`: `BbRouter`, `samlCookie`, `AWSALB`, and two `JSESSIONID`s with different paths
  (`/auth-saml/` and `/learn/api`). Load cookies with their domain and path (`Blackboard._jar`); upstream's
  name-to-value dict merged the two `JSESSIONID`s into one. The headless silent refresh hasn't been tested on NTU.
- File downloads leave the host. `…/attachments/{id}/download` returns 302 to `/bbcswebdav/…`, then 302 to
  `alt-5dcb73f79ba4c.blackboard.com/bbcswebdav/…`, then 302 to the file. The alt host needs no NTU cookies.
  Upstream loaded cookies without a domain, so httpx sent the whole session there. Don't go back to domainless
  cookies.
- Content is mostly `.pptx` (SC6106 has no PDFs at all). Courses also hold stray files like `styles.css`, so sync
  uses an allowlist (`STUDY_EXTS`) rather than taking everything.
- Week folders are named `W1 …`, `W2 …`. The third `WEEK` regex in `sync.py` handles this; make sure "Web3"
  still doesn't match it.
- An Ultra Document is a folder with an `ultraDocumentBody` child that holds the body and its embedded
  `bbcswebdav` files. `Blackboard.content_files` looks one level into a folder for that child.
- Course names look like `AY2627-T2-SC6106-NAME` or `AY2627-T2-Aug(T)-SC6131-NAME`. `NTU_TERM_PREFIX` in `bb.py`
  drops everything before the code in `short_name()`.
- Gradebook columns come from v2 `courses/{id}/gradebook/columns`, your grades from v1
  `courses/{id}/gradebook/users/{userId}`. Most courses use it; SC6104, SC6107, SC6108 and SC6128 have no columns.
- The calendar is thin. Quiz and project deadlines are often only written in the item title and have no
  `grading.due`, so `bb_upcoming` doesn't see them.
- The Media Gallery shows up in `bb_browse` as handler `x-bb-bltiplacement-NewUCVGalleryLTI_1_1`. Despite the
  name it's Kaltura's LTI 1.3 tool on `ntulearnv1.ntu.edu.sg`. See "Lecture recordings" below.

## Running and checking changes

```bash
uv sync                                         # .venv with the project installed editable
export BB_MCP_HOME="$PWD/.bb-home" BB_DOWNLOAD_DIR="$PWD/downloads"   # from the repo root
.venv/Scripts/blackboard-mcp.exe login          # opens Chrome; the user completes SSO + MFA
.venv/Scripts/blackboard-mcp.exe status         # or: courses, sync SC6106
uv run --with pytest pytest                     # upstream's offline tests
```

To check a tool against live data, call it directly from `.venv/Scripts/python.exe`, e.g.
`anyio.run(lambda: server.bb_read("SC6106", content_id, None, 0, 2000))`. Set `PYTHONIOENCODING=utf-8` on
Windows: announcement titles contain emoji. The global `blackboard-mcp` tool is an editable install of this
checkout (`uv tool install --editable . --force`), so code changes apply without reinstalling.

Don't add tests unless asked. Check changes by running them against NTULearn.

## Lecture recordings (Kaltura)

`bb_recordings` and `bb_transcript` go through `server._with_gallery`, which keeps one `kaltura.Gallery` per course
for three hours and relaunches once if Kaltura rejects the session. Opening a gallery (`kaltura.open_gallery`) takes
about 30 seconds and runs headless under `auth.PROFILE_LOCK`, since Chrome allows one process per profile. Verified
on SC6106 and SC6104 on 2026-11-17:

1. `Blackboard.media_gallery` finds the gallery item and reads its launch link from the **private** API
   (`/learn/api/v1/courses/{id}/contents/{id}` → `contentDetail[handler].launchLink`, e.g.
   `/webapps/blackboard/execute/blti/launchPlacement?blti_placement_id=_1327_1&content_id=…&course_id=…`) and the
   tool host from `domainConfig.primaryDomain`. The public API only gives the placement handle, and the item's
   `/ultra/redirect` link never opens the tool in a headless browser.
2. Opening that link top-level (minus `&wrapped=true`) loads `contentWrapper.jsp` with the gallery in a frame on
   `ntulearnv1.ntu.edu.sg/channel/<course>/<id>`. The saved cookies are added to the Playwright context first,
   because the persistent profile can drop Blackboard's session cookies between runs.
3. Entry IDs come off the frame's HTML (thumbnail URLs, `/media/t/<id>` links). Kaltura can't list a channel for a
   KAF session (see the extension's notes), so the page is the only source.
4. The gallery HTML's KS candidates all fail (`INVALID_ACTIONS_LIMIT` or `INVALID_KS`). One of the five on a
   lecture page (`/media/t/<id>`, fetched with `ctx.request`) is a full user session. Probe each with
   `baseentry.list`; length doesn't identify it.
5. With that KS: `baseentry.list` (names, durations, `createdAt`), `parentEntryIdIn` for second streams,
   `caption_captionasset.list` with `entryIdIn` (status 2 = ready), and `getUrl`, which returns a token-free CDN
   URL to an SRT. `captions_to_text` turns it into minute-stamped paragraphs.

The Kaltura facts behind this (partner 117, `api.sg.kaltura.com`, the decoy `analytics.*` host, captions on the
parent entry, `playManifest` for video) are in `D:/Work/ntulearn-media-gallery-downloader/CLAUDE.md`. Video
download isn't exposed as a tool; the extension does that. Gallery "load more" paging is handled but untested,
because every gallery so far fit on one page (12 recordings).
