# agent-browser Guide

CLI browser automation tool. Uses a ref-based accessibility tree for deterministic element selection. Connects to a persistent browser daemon.

---

## Setup & Session Management

```bash
# Official install:
npm install -g agent-browser
agent-browser install

# Start with a logged-in Chrome profile (required for Gmail, etc.)
agent-browser --profile /home/fipauli/.chrome-profile open https://example.com

# The daemon persists — profile flag is IGNORED if daemon is already running
# To restart with a different profile:
agent-browser close
agent-browser --profile /home/fipauli/.chrome-profile open https://example.com
```

If Linux browser dependencies are missing:

```bash
agent-browser install --with-deps
```

**Key rule:** `--profile` only applies at daemon start. If the daemon is already running from a previous command (even a different session), it is silently ignored. Always `close` first if the profile matters.

For this repo's logged-in Chrome workflow on recent Chrome versions:

- open Chrome first
- open `chrome://inspect/#remote-debugging`
- allow remote debugging
- on the first controlled run, click **Allow** so `agent-browser` can control the live logged-in session
- otherwise a fresh browser instance may be launched instead of the logged-in session

Prefer `--auto-connect` for this workflow because Chrome may expose a dynamic DevTools port. Also prefer a named session to avoid stale default-session state:

```bash
agent-browser --session live --auto-connect open https://example.com
AGENT_BROWSER_SESSION=live agent-browser snapshot -c
```

If Chrome was explicitly started with `--remote-debugging-port=9222`, use CDP mode as a fallback:

```bash
agent-browser connect 9222
agent-browser --cdp 9222 snapshot
```

To inspect session state:

```bash
agent-browser session list
agent-browser session
```

---

## Core Workflow

```bash
agent-browser open https://example.com
agent-browser wait --load networkidle      # wait for JS-heavy pages
agent-browser snapshot -i                  # get interactive elements + refs
# → @e1 [button] "Submit", @e2 [input] ...
agent-browser click @e2
agent-browser fill @e3 "text"
agent-browser snapshot -i                  # re-snapshot after DOM changes (refs invalidate)
```

**Refs invalidate** after any navigation or significant DOM change. Always re-snapshot before using refs from a previous snapshot.

---

## Snapshot Flags

| Flag | Description |
|------|-------------|
| `-i` | Interactive elements only (buttons, links, inputs) — best for finding clickables |
| `-c` | Compact output (removes empty structure) — use for content extraction |
| `-d N` | Limit tree depth to N levels |
| `-s "#sel"` | Scope to CSS selector |
| `--json` | JSON output |
| `--max-output N` | Truncate to N characters (avoid overflowing context) |

**For headline/content extraction:** use `-c` (compact) without `-i` to see text nodes too.

---

## Bulk Content Extraction

To extract multiple headlines or text items in one pass, snapshot with `-c` and filter:

```bash
agent-browser snapshot -c 2>&1 | grep -E "heading|link" | grep -v "nav\|menu\|logo" | head -20
```

Or extract programmatically with `eval`:

```bash
agent-browser eval "
  Array.from(document.querySelectorAll('h2, h3, article a'))
    .slice(0, 10)
    .map(el => el.textContent.trim())
    .filter(t => t.length > 20)
    .join('\n')
"
```

This is much faster than clicking each item individually.

---

## Key Commands

```bash
# Navigation
agent-browser open <url>
agent-browser back / forward / reload
agent-browser wait --load networkidle
agent-browser wait @e1              # wait for element
agent-browser wait 2000             # wait ms
agent-browser wait --url "pattern"

# Interaction
agent-browser click @e1
agent-browser fill @e1 "text"       # clear + type
agent-browser type @e1 "text"       # type without clearing
agent-browser press Tab / Enter / Escape
agent-browser press "Control+v"
agent-browser scroll down 500
agent-browser upload @e1 /path/to/file   # NOTE: fails on hidden file inputs (e.g. Gmail)

# Reading
agent-browser get text @e1
agent-browser get attr @e1 href
agent-browser get url
agent-browser eval "document.title"

# Screenshots
agent-browser screenshot output.png
agent-browser screenshot --full
agent-browser screenshot --annotate   # adds numbered labels for debugging
```

---

## Semantic Locators (alternative to refs)

More robust than refs — don't invalidate on DOM changes:

```bash
agent-browser find role button click --name "Submit"
agent-browser find text "Sign in" click
agent-browser find label "Email" fill "a@b.com"
agent-browser find placeholder "Search" fill "query"
```

---

## Practical Patterns

### Extracting news headlines

```bash
agent-browser open https://www.jovempan.com.br
agent-browser wait --load networkidle
# Compact snapshot + grep for headings/links, exclude nav chrome
agent-browser snapshot -c 2>&1 | grep -E "heading|link" \
  | grep -v -i "menu\|logo\|login\|assine\|newsletter\|instagram\|facebook\|twitter\|youtube\|podcast" \
  | head -30
```

### Navigating JS-heavy sites (e.g. Simepar)

```bash
agent-browser open https://www.simepar.br/simepar/forecast_by_counties/4104808
agent-browser wait --load networkidle
agent-browser wait 3000             # extra wait for JS rendering
agent-browser snapshot -c -d 5
```

### Before declaring a site inaccessible

Use this fallback sequence before giving up:

```bash
agent-browser open <url>
agent-browser wait --load networkidle
agent-browser wait 2000
agent-browser snapshot -c --max-output 8000
agent-browser eval "document.title"
agent-browser get url
```

If the page still looks empty or broken:

- try a nearby URL such as the homepage, `/world`, `/business`, or another likely section
- prefer extracting visible text with `snapshot -c` or `eval` before concluding the site is blocked
- only declare failure after at least one retry path

Observed site behavior:

- CNBC and WSJ rendered normally in the live browser session.
- CNN was inconsistent: `agent-browser open https://www.cnn.com` could appear to hang, but the page later became readable after the fallback sequence.
- For CNN specifically, do not stop at the `open` result. Wait, re-check title, inspect `get url`, and run `snapshot -c` before concluding failure.

### Filling forms reliably

```bash
# Prefer semantic locators over refs for form fields
agent-browser find label "Email" fill "user@example.com"
agent-browser find label "Senha" fill "password"
agent-browser find role button click --name "Entrar"
```

---

## Pitfalls & Known Issues

### `--profile` silently ignored
If the daemon is already running, `--profile` does nothing. Check with `agent-browser get url` — if it returns an unexpected page, close and restart.

### `upload` fails on hidden file inputs
`agent-browser upload @eN /path` requires the ref to point to an `<input type="file">` element. On sites like Gmail, the file input is hidden and the button ref won't work. Use `mcp__chrome-devtools__upload_file` instead.

### Refs invalidate after navigation
After any `open`, `click` that triggers navigation, or major DOM update, all `@eN` refs are stale. Always re-snapshot.

### CDP timeout on slow/heavy pages
If `agent-browser open` returns a CDP timeout error, the daemon may be in a bad state. Run `agent-browser close` and retry.

### `open` appears hung, but the page may still load
Some sites can leave `agent-browser open` hanging even though navigation succeeded in the browser. Verify with:

```bash
agent-browser get url
agent-browser wait --load networkidle
agent-browser wait 2000
agent-browser eval "document.title"
agent-browser snapshot -c --max-output 8000
```

Do not declare failure until these checks also fail.

### Snapshot too large
For content-heavy pages, use `--max-output 8000` or scope with `-s "#main-content"` to avoid overflowing the context window.

---

## When to use agent-browser vs Chrome MCP

| Task | Use |
|------|-----|
| Extracting text/headlines from public sites | agent-browser |
| Interacting with logged-in Gmail | Chrome MCP (more reliable) |
| File upload in Gmail | Chrome MCP (`upload_file`) |
| JS eval / clipboard operations | Chrome MCP (`evaluate_script`) |
| Screenshots for verification | Either |
| Sites that time out via MCP | agent-browser (separate daemon) |
