# multiclaude

Fan out a task across multiple parallel Claude Code sessions, each in its
own git worktree and Terminal tab, then synthesize their outputs with a
final "judge" pass.

Each session gets a variant of a base prompt (the base alone, plus one
session per helper modifier). The script polls each session's transcript
JSONL until it reaches `end_turn`, then pipes all of the final messages
into a `claude -p` call that produces a clustered comparison and a
synthesized recommendation.

## Requirements

- macOS (controls Terminal.app via AppleScript / `osascript`)
- [Claude Code CLI](https://claude.com/claude-code) installed and on `PATH`
- Run from inside a git repository (the script creates worktrees under
  `.worktrees/`)
- Python 3.10+

No third-party Python packages are needed — only the standard library.

## Usage

From the root of the git repo you want the sessions to operate on:

```sh
python3 evaluate.py
```

Pass `--auto-clean` to remove the worktrees automatically once the judge
output is printed:

```sh
python3 evaluate.py --auto-clean
```

If you skip `--auto-clean` (or interrupt the run with Ctrl-C), clean up
leftover worktrees later with:

```sh
python3 cleanup.py
```

`cleanup.py` finds and removes any worktree whose path contains
`/multiclaude-`, so it is safe to run from any branch.

## Customizing the prompts

Edit the constants at the top of `evaluate.py`:

- `BASE_PROMPT` — the task all sessions share.
- `HELPER_PROMPTS` — list of additional modifiers. Each one launches a
  separate session whose prompt is `f"{BASE_PROMPT} {helper}"`. Total
  sessions launched = `1 + len(HELPER_PROMPTS)`.

Other knobs in the same file:

- `CMD` — the Claude command line used per session (defaults to
  `claude --permission-mode auto`).
- `SESSION_TIMEOUT` — per-session wait limit before the script gives up
  on `end_turn` and uses the latest assistant message instead.
- `READY_TIMEOUT` / `READY_MARKER` — how long to wait for the Claude REPL
  to render `auto mode on` in its footer before pasting the prompt.

## Caveats

- macOS only. AppleScript and Terminal.app are required.
- The script focuses Terminal and sends keystrokes via System Events
  while launching, so it will briefly steal focus.
- Sessions run with `--permission-mode auto`. Make sure that's what you
  want before pointing it at sensitive worktrees.

## License

MIT — see `LICENSE`.
