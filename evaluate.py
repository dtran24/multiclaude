#!/usr/bin/env python3
import json
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path

from cleanup import remove_worktrees

CMD = "claude --permission-mode auto"
BASE_PROMPT = "find the root cause of #incident-123 https://app.slack.com/client/T0330U0RUEC/D0BFMBBM0GG"
HELPER_PROMPTS = [
    "use gh cli and Datadog to justify findings",
    "review findings with /codex:rescue",
]
POLL_INTERVAL = 2.0
SESSION_TIMEOUT = 600.0
# claude's REPL renders "auto mode on" in its footer once it's accepting
# input. We poll the tab's visible content for that marker as the ready
# signal — the session jsonl can't be used because claude doesn't create it
# until after the first user prompt is submitted.
READY_TIMEOUT = 30.0
READY_MARKER = "auto mode on"
PROJECTS_DIR = Path.home() / ".claude" / "projects"
WORKTREE_ROOT = Path.cwd() / ".worktrees"


def applescript_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def make_worktree(session_id: str) -> Path:
    short = session_id[:8]
    path = WORKTREE_ROOT / f"multiclaude-{short}"
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(path)],
        check=True,
    )
    return path


def open_tab_script(session_id: str, cwd: Path) -> str:
    shell_cmd = f"cd {shlex.quote(str(cwd))} && {CMD} --session-id {session_id}"
    cmd = applescript_escape(shell_cmd)
    return f'''
    tell application "Terminal"
        activate
    end tell
    tell application "System Events"
        tell process "Terminal"
            keystroke "t" using command down
        end tell
    end tell
    delay 0.3
    tell application "Terminal"
        do script "{cmd}" in front window
    end tell
    '''


def paste_prompt_script(prompt: str) -> str:
    p = applescript_escape(prompt)
    return f'''
    set the clipboard to "{p}"
    tell application "Terminal"
        activate
    end tell
    tell application "System Events"
        tell process "Terminal"
            keystroke "v" using command down
            delay 0.2
            key code 36
        end tell
    end tell
    '''


def wait_for_tab_ready(timeout: float = READY_TIMEOUT) -> bool:
    deadline = time.time() + timeout
    script = (
        'tell application "Terminal" to '
        "return contents of selected tab of front window"
    )
    while time.time() < deadline:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
        )
        if READY_MARKER in result.stdout:
            return True
        time.sleep(0.3)
    return False


def launch_phase(
    base: str, helpers: list[str]
) -> list[tuple[str, str, Path]]:
    prompts = [base] + [f"{base} {h}" for h in helpers if h.strip()]
    triples: list[tuple[str, str, Path]] = []
    for prompt in prompts:
        sid = str(uuid.uuid4())
        wt = make_worktree(sid)
        subprocess.run(
            ["osascript", "-e", open_tab_script(sid, wt)],
            check=True,
        )
        if not wait_for_tab_ready():
            print(
                f"[warn] session {sid} not ready within {READY_TIMEOUT}s; "
                f"pasting anyway",
                file=sys.stderr,
            )
        subprocess.run(
            ["osascript", "-e", paste_prompt_script(prompt)],
            check=True,
        )
        triples.append((sid, prompt, wt))
    return triples


def jsonl_path(session_id: str, cwd: Path) -> Path:
    # claude encodes the cwd by replacing both "/" and "." with "-", so e.g.
    # /path/to/.worktrees/foo -> -path-to--worktrees-foo. The dot rule
    # matters here because worktrees live under a hidden ".worktrees/" dir.
    encoded = str(cwd).replace("/", "-").replace(".", "-")
    return PROJECTS_DIR / encoded / f"{session_id}.jsonl"


def read_records(path: Path) -> list[dict]:
    records: list[dict] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def find_done_message(records: list[dict]) -> dict | None:
    for rec in reversed(records):
        if rec.get("type") == "assistant":
            msg = rec.get("message") or {}
            if msg.get("stop_reason") == "end_turn":
                return msg
            return None
    return None


def extract_text(message: dict) -> str:
    parts: list[str] = []
    for block in message.get("content", []) or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts).strip()


def latest_assistant_text(records: list[dict]) -> str:
    for rec in reversed(records):
        if rec.get("type") == "assistant":
            text = extract_text(rec.get("message") or {})
            if text:
                return text
    return ""


def wait_phase(
    triples: list[tuple[str, str, Path]], timeout: float
) -> list[dict]:
    results: list[dict] = []
    for sid, prompt, wt in triples:
        path = jsonl_path(sid, wt)
        deadline = time.time() + timeout
        final_text = ""
        done = False
        while time.time() < deadline:
            if path.exists():
                records = read_records(path)
                msg = find_done_message(records)
                if msg is not None:
                    final_text = extract_text(msg)
                    done = True
                    break
            time.sleep(POLL_INTERVAL)
        if not done:
            if path.exists():
                final_text = latest_assistant_text(read_records(path))
            print(
                f"[warn] session {sid} timed out without end_turn",
                file=sys.stderr,
            )
        else:
            print(f"[ok] session {sid} done", file=sys.stderr)
        results.append({"uuid": sid, "prompt": prompt, "final_text": final_text})
    return results


JUDGE_TEMPLATE = """\
You are evaluating outputs from {n} parallel Claude sessions, each given a
variant of the same base task. Compare their conclusions.

# Base task
{base}

# Session outputs
{sessions}

# Required output

Produce two sections, in this order, in markdown.

## 1. Cluster summary
Group the sessions by approach. For each cluster, name the approach and list
which sessions belong to it. Then list the key dimensions on which sessions
agreed and disagreed.

## 2. Synthesized recommendation
Produce a single best-of plan, attributing which ideas came from which session.
This can be either a hybrid (e.g., "session 2's error-handling approach +
session 1's data flow") or picking one session's plan in full when its
justification is more correct than the others (e.g., "session 1's plan as-is,
because its reasoning for choosing approach X over Y is sound and the other
sessions either skip the tradeoff or get it wrong"). Be explicit about why.
"""


def evaluate_phase(base: str, results: list[dict]) -> str:
    nonempty = [r for r in results if r["final_text"]]
    if not nonempty:
        print(
            "[error] no session produced any output; skipping judge.",
            file=sys.stderr,
        )
        return ""

    sessions_md = []
    for i, r in enumerate(nonempty, 1):
        sessions_md.append(
            f"## Session {i} (uuid={r['uuid']})\n"
            f"**Variant prompt**: {r['prompt']}\n\n"
            f"**Output**:\n\n{r['final_text']}\n"
        )
    judge_prompt = JUDGE_TEMPLATE.format(
        n=len(nonempty),
        base=base,
        sessions="\n\n".join(sessions_md),
    )
    proc = subprocess.run(
        ["claude", "-p", "--output-format", "text"],
        input=judge_prompt,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


CLEANUP_HINT = (
    "Cleanup when done (or if interrupted), run:\n"
    "  python3 cleanup.py"
)


def main():
    auto_clean = "--auto-clean" in sys.argv[1:]
    try:
        triples = launch_phase(BASE_PROMPT, HELPER_PROMPTS)
        print(f"Launched {len(triples)} sessions:", file=sys.stderr)
        for sid, prompt, wt in triples:
            print(f"  {sid}  {prompt!r}  {wt}", file=sys.stderr)
        print(f"\n{CLEANUP_HINT}\n", file=sys.stderr)
        print("Waiting for sessions to finish...", file=sys.stderr, flush=True)
        results = wait_phase(triples, SESSION_TIMEOUT)
        print("\n=== Judge output ===\n")
        print(evaluate_phase(BASE_PROMPT, results))
        if auto_clean:
            print("\nAuto-cleaning worktrees from this run...", file=sys.stderr)
            remove_worktrees([str(wt) for _, _, wt in triples])
        else:
            print(f"\n{CLEANUP_HINT}", file=sys.stderr)
    except KeyboardInterrupt:
        print(f"\n[interrupted]\n{CLEANUP_HINT}", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
