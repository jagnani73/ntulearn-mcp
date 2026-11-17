# CLAUDE.md

Working notes for this repository. The NTU-specific behaviour below was checked against the live NTULearn on
2026-11-17; the reasons for most of the fork's changes are in there.

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
- The Media Gallery shows up in `bb_browse` as handler `x-bb-bltiplacement-NewUCVGalleryLTI_1_1`. It's an LTI
  launch into Kaltura on `ntulearnv1.ntu.edu.sg`, which isn't reachable directly.

## Running and checking changes

```bash
uv sync                                         # .venv with the project installed editable
export BB_MCP_HOME=D:/Work/ntulearn-mcp/.bb-home BB_DOWNLOAD_DIR=D:/Work/ntulearn-mcp/downloads
.venv/Scripts/blackboard-mcp.exe login          # opens Chrome; the user completes SSO + MFA
.venv/Scripts/blackboard-mcp.exe status         # or: courses, sync SC6106
uv run --with pytest pytest                     # upstream's offline tests
```

To check a tool against live data, call it directly from `.venv/Scripts/python.exe`, e.g.
`anyio.run(lambda: server.bb_read("SC6106", content_id, None, 0, 2000))`. Set `PYTHONIOENCODING=utf-8` on
Windows: announcement titles contain emoji. The global `blackboard-mcp` tool is an editable install of this
checkout (`uv tool install --editable . --force`), so code changes apply without reinstalling.

Don't add tests unless asked. Check changes by running them against NTULearn.

## Next: lecture recordings (Kaltura)

The Kaltura side is documented in `D:/Work/ntulearn-media-gallery-downloader/CLAUDE.md`: partner 117,
`api.sg.kaltura.com`, video through `playManifest` (not `flavorAsset.getDownloadUrl`, which the KAF session can't
call), dual-stream recordings as parent and child entries, captions on the parent via
`caption_captionAsset.getUrl`, and `media.list` by category returning nothing, so entries come from the gallery
page itself. The open problem is getting a Kaltura session (KS) through the LTI launch inside the Playwright
profile. Captions as text would let the model summarise a lecture.

## Conventions

- Conventional Commits, subject 50 characters or fewer (72 hard ceiling), imperative mood, no trailing period.
- No AI co-author or attribution trailers in commits or PR descriptions.
- Keep this file and `README.md` current when behaviour or NTU findings change.
