# kimi-cli thinking command

Adds a `/thinking` slash command, a `/t` shortcut alias, and a `Ctrl+T` keyboard shortcut to [kimi-cli](https://github.com/MoonshotAI/kimi-cli).

Toggles thinking mode for the current session without leaving the terminal — the session reloads in place with the new state.

The thinking indicator (● / ○) in the status bar reflects the current state. `Ctrl+T` also appears in the rotating toolbar tips.

---

## Requirements

- [kimi-cli](https://github.com/MoonshotAI/kimi-cli) installed
- Python 3.11+

---

## Install

**Linux / macOS**
```bash
curl -fsSL https://raw.githubusercontent.com/<user>/<repo>/main/kimi-cli-thinking-command.py | python3 - apply
```

**Windows (PowerShell)**
```powershell
irm https://raw.githubusercontent.com/<user>/<repo>/main/kimi-cli-thinking-command.py | python - apply
```

Then restart kimi-cli. `/thinking`, `/t`, and `Ctrl+T` are immediately available.

---

## Commands

| Action | Linux / macOS | Windows (PowerShell) |
|---|---|---|
| Apply patch | `... \| python3 - apply` | `... \| python - apply` |
| Restore original | `... \| python3 - restore` | `... \| python - restore` |
| Check status | `... \| python3 - status` | `... \| python - status` |

Replace `...` with the full `curl -fsSL <url>` or `irm <url>` command above.

---

## After a kimi-cli upgrade

The patch targets the installed site-package. A `uv tool upgrade kimi-cli` overwrites it. Just re-run `apply`.

---

## How it works

The installer patches two files inside the kimi-cli package:

- **`ui/shell/slash.py`** — registers the `/thinking` command (alias `/t`) in the slash command registry. When invoked, it checks whether the current model supports thinking mode, toggles `default_thinking` in the config file, and reloads the session.
- **`ui/shell/prompt.py`** — adds a `Ctrl+T` key binding that submits `/thinking` programmatically, and appends `ctrl-t: thinking` to the rotating toolbar tips.

Both files are backed up before patching (`*.py.backup`). If a syntax error is detected after patching, the backup is automatically restored.

---

## Compatibility

| Platform | Supported |
|---|---|
| Linux | ✓ |
| macOS | ✓ |
| Windows (PowerShell) | ✓ |
| Windows (WSL) | ✓ |
