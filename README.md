# NTULearn MCP

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-server-8A2BE2)](https://modelcontextprotocol.io)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)

A read-only [MCP](https://modelcontextprotocol.io) server for **NTULearn**, NTU Singapore's Blackboard Learn
(Ultra). Claude Code, Claude Desktop, Cursor or any other MCP client can use it to list your courses, browse and read
course files, check deadlines, announcements and grades, and sync a semester's material to disk.

It's a fork of [Mattlo0546/blackboard-mcp](https://github.com/Mattlo0546/blackboard-mcp), which already had the
hard part working: signing in through the university's SSO and MFA in a real browser window, with no API key and no
cookie copying. The fork adds what NTU needed:

- `bb_read` returns the text of course files without saving them: slides with speaker notes, PDFs, Word, Excel
  and code. The extractors are adapted from [kingdonkatsu/ntulearn-mcp](https://github.com/kingdonkatsu/ntulearn-mcp).
- `bb_grades` lists every gradebook item with points possible, due date, status and feedback.
- Sync works on NTU courses. Upstream synced PDFs only, and NTU lecturers mostly upload `.pptx`, so sync now takes
  slides, documents, spreadsheets, notebooks, archives and code. It also files NTU's `W1`, `W2` folders under
  `Week 01`, `Week 02`.
- Session cookies stay on NTULearn. File downloads redirect to Blackboard's separate file host
  (`alt-….blackboard.com`), and upstream sent your session cookies there too. Each cookie is now scoped to its own
  domain and path.
- Downloads work on Windows. Long course names used to be cut off with a trailing space, which Windows won't
  accept in a folder name.
- Course names are shorter: `AY2627-T2-SC6106-BLOCKCHAIN DEVELOPMENT FUNDAMENTALS - PART 1` becomes
  `SC6106 BLOCKCHAIN DEVELOPMENT FUNDAMENTALS - PART 1`, in tool output and in folder names.

Lecture recordings aren't covered yet. The course Media Gallery is a separate Kaltura site (see
[Roadmap](#roadmap)).

## Setup

You need [uv](https://docs.astral.sh/uv/) and Google Chrome.

```bash
git clone <this repo> ntulearn-mcp && cd ntulearn-mcp
uv sync
uv tool install --editable .        # puts `blackboard-mcp` on your PATH, running this checkout
```

Sign in once. A Chrome window opens on NTULearn; finish the NTU sign-in and MFA there. It uses its own browser
profile, separate from your everyday Chrome.

```bash
BB_MCP_HOME=/path/to/ntulearn-mcp/.bb-home blackboard-mcp login
```

Then add it to your MCP client with the same `BB_MCP_HOME`. For Claude Code:

```bash
claude mcp add ntulearn -s user \
  -e BB_MCP_HOME=/path/to/ntulearn-mcp/.bb-home \
  -e BB_DOWNLOAD_DIR=/path/to/ntulearn-mcp/downloads \
  -- blackboard-mcp serve
```

Claude Desktop and Cursor take the same command, args (`["serve"]`) and `env` in their JSON config.

When the session expires, tools answer with `login_required`. The server first tries to refresh it silently
using the saved browser profile, and if that fails, `bb_login` opens the window again.

## Tools

| Tool | What it does |
| --- | --- |
| `bb_login` | Signs in through a browser window. Tries a silent refresh first. |
| `bb_status` | Who you're signed in as, where files download to, when the last sync ran |
| `bb_courses` | This year's courses (`include_all` for every course and organisation) |
| `bb_browse` | A course's content, or one folder's: titles, types, text, links, grade columns |
| `bb_read` | The text of one item's files (pptx with speaker notes, pdf, docx, xlsx, text and code), paged with `offset` |
| `bb_download` | Saves every file on one content item |
| `bb_upcoming` | Due dates from the Blackboard calendar for the next N days (default 21) |
| `bb_announcements` | Recent announcements, for one course or all current ones |
| `bb_grades` | Gradebook items with points possible, due date, your score, status and feedback |
| `bb_sync` / `bb_sync_status` | Downloads study files from this year's courses in the background. Only new or changed files are fetched. |
| `bb_set_courses` | Changes which courses a full sync pulls |

Things that are useful to know on NTULearn:

- Many quiz and project deadlines are only written in the item title ("Week 3 Quiz (due end of week 5 Sunday
  2359)") and never reach the calendar, so `bb_upcoming` misses them. `bb_grades` lists those items, titles
  included.
- `bb_sync` skips sites that aren't courses (the academic integrity module, for example) and files that aren't for
  studying, like posters, handbooks and forms. `bb_set_courses` brings a skipped course back.

## Settings

All optional. Environment variables override `<BB_MCP_HOME>/config.json`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `BB_BASE_URL` | `https://ntulearn.ntu.edu.sg` | Blackboard address. Any other Blackboard Learn site works too. |
| `BB_MCP_HOME` | `~/.blackboard-mcp` | Session cookies, the sign-in browser profile and sync state |
| `BB_DOWNLOAD_DIR` | `~/BlackboardFiles` | Where downloads and syncs go: `<course>/<Week NN or folder>/<file>` |
| `BB_BROWSER_CHANNEL` | `chrome` | Browser for sign-in; `""` uses Playwright's own Chromium |
| `BB_SSO_BUTTON` | a few common labels | Text of the landing-page button that starts SSO, if the silent refresh needs one |

## Privacy

- It's read-only. It only makes GET requests and can't submit, post or change anything on NTULearn.
- Your session lives in `BB_MCP_HOME`. `cookies.json` holds it in plain text (Windows ignores the `chmod 600`),
  and `browser-profile/` holds the sign-in browser's state. Anyone who can read that folder can act as you on
  NTULearn until the session expires. Delete the folder to sign out.
- Cookies only go to `ntulearn.ntu.edu.sg`. Downloads still work when the redirect lands on Blackboard's file
  host, because that host doesn't need them.
- Requests carry a `blackboard-mcp/0.1` user agent, so NTU's logs can tell them apart from a browser.
- Your MCP client sees whatever the tools return. Course content, announcements and grades go to the model
  provider like any other tool result.

Using your session this way may not fit NTU's IT acceptable use policy. That's your call.

## Roadmap

Lecture recordings and their captions, from the course Media Gallery. The gallery is NTU's Kaltura MediaSpace,
opened through an LTI launch from the course (`x-bb-bltiplacement-NewUCVGalleryLTI_1_1` in `bb_browse`). The
Kaltura side is already worked out in
[ntulearn-media-gallery-downloader](https://github.com/jagnani73/ntulearn-media-gallery-downlaoder). What's still
open is getting a Kaltura session from the Playwright browser through that launch.

## Development

```bash
uv sync
uv run --with pytest pytest     # upstream's offline tests
```

`git remote` has `upstream` pointing at Mattlo0546/blackboard-mcp. See [CLAUDE.md](CLAUDE.md) for how the code is
laid out and what was verified against the live NTULearn.

## Credits

- [Mattlo0546/blackboard-mcp](https://github.com/Mattlo0546/blackboard-mcp) (MIT): the base of this fork
- [kingdonkatsu/ntulearn-mcp](https://github.com/kingdonkatsu/ntulearn-mcp) (MIT): the file text extractors in
  `blackboard_mcp/extract.py`

Not affiliated with NTU or Blackboard. MIT licence; see [LICENSE](LICENSE).
