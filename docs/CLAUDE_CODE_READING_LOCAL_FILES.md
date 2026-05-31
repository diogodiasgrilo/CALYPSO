# Letting Claude Code read many local files without a permission prompt on each one

**Problem this solves:** You have a big pile of local files you want Claude to read in bulk
— extracted video frames, a screenshot dump, a folder of logs, exported data — and Claude
prompts you for permission on **every single Read**. With hundreds of files that's
unusable.

This doc captures the working solution and *why the obvious fixes don't work*, so you don't
re-derive it every time.

---

## TL;DR — the fix that actually works

**Put the files inside the project/workspace folder.** Reads of files **inside the open
workspace are auto-allowed**; reads of files **outside it** (e.g. `/tmp`, `~/Downloads`)
prompt every time.

```bash
# Example: frames extracted to /tmp/zoomaudit — move them into the repo, gitignored.
mkdir -p /Users/you/Desktop/myproject/.scratch
cp -R /tmp/zoomaudit/frames /Users/you/Desktop/myproject/.scratch/frames
echo ".scratch/" >> /Users/you/Desktop/myproject/.gitignore
```

Now Claude reads `/Users/you/Desktop/myproject/.scratch/frames/*.jpg` with **no prompts**.
Use a dot-prefixed, gitignored folder (`.scratch/`, `.zoomaudit/`, `.local-data/`) so the
bulk files never get committed.

That's it. Everything below is the *why* and the alternatives for cases where you can't
move the files.

---

## Why the "obvious" fixes fail

### ❌ Editing `.claude/settings.local.json` mid-session
Adding a rule like:
```json
{ "permissions": { "allow": ["Read(/tmp/zoomaudit/**)"] } }
```
is *correct on disk* but **does not take effect in the running session** when the file is
written by Claude's own Write/Edit tool. This is a known bug:
[anthropics/claude-code#41259](https://github.com/anthropics/claude-code/issues/41259) —
the in-memory permission cache isn't reloaded after the Edit tool writes the file.

Extra trap: the settings **file-watcher only watches `.claude/` directories that existed
when the session started.** If `.claude/` is created *during* the session, the new file is
never watched at all.

### ❌ `/hooks` to "reload" settings
Opening `/hooks` does **not** reliably clear the stale permission cache. It's not a full
config reload.

### ❌ Running `claude --continue` in a side terminal
In the VS Code extension, the "Permissions"/terminal button opens a **plain shell**. Running
`claude --continue --add-dir ...` there starts a **separate CLI session in that terminal
tab**. If you then go back to typing in the graphical chat panel, you're still in the old
session — the terminal session's relaxed permissions never reach the panel you're using.
(It *would* work if you actually did your work in that terminal session.)

---

## Alternatives when you genuinely can't move the files into the workspace

Ranked by how clean they are.

### 1. Restart so settings load fresh (most reliable for a persistent rule)
The `settings.local.json` rule is correct — it just needs a clean load.

- **CLI:** quit and relaunch with `claude --continue` (resumes the same conversation; the
  extension and CLI share history). Optionally add `--add-dir /abs/path` to grant a working
  directory at launch.
- **VS Code extension:** Command Palette (`Cmd/Ctrl+Shift+P`) → **Developer: Reload Window**.
  Then click **Session history** at the top of the Claude panel and reopen the conversation
  (full history is preserved).

After a fresh start the watcher sees `.claude/settings.local.json` at startup, so the
`Read(...)` and `Agent` rules are live.

### 2. `--add-dir` / `additionalDirectories` (grant a working directory)
A path added as an *additional working directory* is treated like the workspace — reads
inside it don't prompt.

- **CLI flag:** `claude --continue --add-dir /tmp/zoomaudit`
- **Settings:** `{ "permissions": { "additionalDirectories": ["/tmp/zoomaudit"] } }`
  (still subject to the "load at startup" caveat above)

### 3. Bypass-permissions mode (nuclear, no restart, no terminal)
In the **VS Code prompt box**, click the **permission-mode indicator** at the bottom and
select **Bypass permissions**. Stops *all* prompts instantly for the current conversation.

If "Bypass permissions" isn't in the list, enable it first:
`Cmd/Ctrl+,` → Extensions → **Claude Code** → check **`allowDangerouslySkipPermissions`**.

Bypass mode is intended for sandboxed / no-risk work. Fine for read-only analysis of local
files; switch back to **default** when done. Don't leave it on for sessions that edit code
or run shell commands you wouldn't auto-approve.

---

## Correct permission-rule syntax (for reference)

These go in `.claude/settings.local.json` (project-local, gitignored) or `~/.claude/settings.json`
(global). Remember the **restart caveat** — add them, then start a fresh session.

```json
{
  "$schema": "https://json.schemastore.org/claude-code-settings.json",
  "permissions": {
    "allow": [
      "Read(/Users/you/Desktop/myproject/**)",   // everything in a folder, recursively
      "Read(/tmp/zoomaudit/**)",                  // an out-of-workspace folder
      "Read(//absolute/path/**)",                 // some versions want a leading // for absolute
      "Agent"                                      // let Claude spawn subagents without prompting
    ],
    "additionalDirectories": ["/tmp/zoomaudit"]
  }
}
```

Notes:
- `Read(dir/**)` is glob-style; `**` matches subdirectories recursively.
- `Agent` (bare tool name) allows spawning subagents — a **separate** permission from `Read`.
  Spawning a fleet of read-only verifier agents will prompt unless this is allowed (and loaded
  via a fresh session).
- Add `.claude/settings.local.json` to `.gitignore` — it's a personal/local override.

---

## The mental model

Two independent gates were conflated in the session that produced this doc:

1. **`Read` of a file** → allowed automatically **inside the workspace**, prompts **outside** it.
2. **`Agent` (spawn subagent)** → always prompts unless explicitly allowed; *not* the same
   permission as Read, even though a subagent's job description may mention "reading files."

And one bug: **settings written by the Edit tool mid-session don't reload** (#41259), so the
only dependable ways to change behavior live are **(a) move files into the workspace**,
**(b) restart**, or **(c) flip the permission mode in the UI**.

For bulk local-file reading, **(a) is almost always the simplest** — copy into a gitignored
`.scratch/` folder and you never touch permissions at all.

---

## Sources
- [Claude Code settings — watch/reload behavior, `additionalDirectories`, `--add-dir`](https://code.claude.com/docs/en/settings)
- [Use Claude Code in VS Code — permission modes, `allowDangerouslySkipPermissions`, Reload Window, Session history](https://code.claude.com/docs/en/vs-code)
- [#41259 — settings.local.json not respected after Edit tool modifies it](https://github.com/anthropics/claude-code/issues/41259)
- [#33829 — hot-reload permissions without restart](https://github.com/anthropics/claude-code/issues/33829)
- [#34320 — no in-chat restart yet (Reload Window is the workaround)](https://github.com/anthropics/claude-code/issues/34320)
