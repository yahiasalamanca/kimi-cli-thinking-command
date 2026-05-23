#!/usr/bin/env python3
"""
kimi-cli /thinking command installer
======================================
Adds a /thinking (alias /t) slash command and a Ctrl+T keyboard shortcut
to the local kimi-cli installation.

Usage (Linux / macOS):
    curl -fsSL https://raw.githubusercontent.com/yahiasalamanca/kimi-cli-thinking-command/main/kimi-cli-thinking-command.py | python3 - apply
    curl -fsSL https://raw.githubusercontent.com/yahiasalamanca/kimi-cli-thinking-command/main/kimi-cli-thinking-command.py | python3 - restore
    curl -fsSL https://raw.githubusercontent.com/yahiasalamanca/kimi-cli-thinking-command/main/kimi-cli-thinking-command.py | python3 - status

Usage (Windows PowerShell):
    irm https://raw.githubusercontent.com/yahiasalamanca/kimi-cli-thinking-command/main/kimi-cli-thinking-command.py | python - apply
    irm https://raw.githubusercontent.com/yahiasalamanca/kimi-cli-thinking-command/main/kimi-cli-thinking-command.py | python - restore
    irm https://raw.githubusercontent.com/yahiasalamanca/kimi-cli-thinking-command/main/kimi-cli-thinking-command.py | python - status

Both patches are applied to the installed site-package and will be overwritten
whenever you run `uv tool upgrade kimi-cli` (or equivalent). Just re-run
`apply` after upgrading.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

# ── File paths inside the kimi_cli package ────────────────────────────────────

TARGET_SLASH = "ui/shell/slash.py"
TARGET_PROMPT = "ui/shell/prompt.py"

# ── Slash command patch ────────────────────────────────────────────────────────

PATCH_MARKER_SLASH = "@registry.command(aliases=[\"t\"])"

PATCH_CODE_SLASH = '''@registry.command(aliases=["t"])
async def thinking(app: Shell, args: str):
    """Toggle thinking mode for the current model"""
    from kimi_cli.llm import derive_model_capabilities

    soul = ensure_kimi_soul(app)
    if soul is None:
        return
    config = soul.runtime.config

    if not config.is_from_default_location:
        console.print(
            "[yellow]Thinking toggle requires the default config file; "
            "restart without --config/--config-file.[/yellow]"
        )
        return

    curr_model_cfg = soul.runtime.llm.model_config if soul.runtime.llm else None
    if curr_model_cfg is None:
        console.print("[yellow]No model currently loaded.[/yellow]")
        return

    capabilities = derive_model_capabilities(curr_model_cfg)
    if "always_thinking" in capabilities:
        console.print("[yellow]Current model always uses thinking mode.[/yellow]")
        return
    if "thinking" not in capabilities:
        console.print("[yellow]Current model does not support thinking mode.[/yellow]")
        return

    curr_thinking = soul.thinking
    new_thinking = not curr_thinking

    prev_thinking = config.default_thinking
    config.default_thinking = new_thinking
    try:
        config_for_save = load_config()
        config_for_save.default_thinking = new_thinking
        config_for_save.default_model = config.default_model
        save_config(config_for_save)
    except (ConfigError, OSError) as exc:
        config.default_thinking = prev_thinking
        console.print(f"[red]Failed to save config: {exc}[/red]")
        return

    from kimi_cli.telemetry import track
    track("thinking_toggle", enabled=new_thinking)
    console.print(
        f"[green]Thinking mode {'enabled' if new_thinking else 'disabled'}. "
        "Reloading...[/green]"
    )
    raise Reload(session_id=soul.runtime.session.id)


'''

# ── Prompt patch (Ctrl+T key binding + toolbar tip) ───────────────────────────

PATCH_MARKER_PROMPT = '"ctrl-t: thinking mode"'

# Inserted just before the existing @_kb.add("c-x") binding.
PATCH_KB = '''        @_kb.add("c-t", eager=True)
        def _(event: KeyPressEvent) -> None:
            if self._active_prompt_delegate() is not None:
                return
            event.current_buffer.set_document(Document(text="/thinking"), bypass_readonly=True)
            event.current_buffer.validate_and_handle()

'''

KB_ANCHOR = '        @_kb.add("c-x", eager=True)\n'
TIPS_ANCHOR = '        "ctrl-x: toggle mode",\n'
TIPS_INSERT = '        "ctrl-t: thinking mode",\n'


# ── Package discovery ─────────────────────────────────────────────────────────

def find_kimi_cli_root() -> Path | None:
    """Locate the installed kimi_cli package root directory."""
    # Strategy 1: importlib (works when running inside the same env)
    spec = importlib.util.find_spec("kimi_cli")
    if spec is not None and spec.origin is not None:
        root = Path(spec.origin).parent
        if (root / TARGET_SLASH).exists():
            return root

    # Strategy 2: uv tool dir
    try:
        result = subprocess.run(
            ["uv", "tool", "dir", "kimi-cli"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            uv_dir = Path(result.stdout.strip())
            # Linux / macOS: lib/python3.x/site-packages/
            for candidate in uv_dir.glob("lib/python*/site-packages/kimi_cli"):
                if (candidate / TARGET_SLASH).exists():
                    return candidate
            # Windows: Lib/site-packages/
            candidate = uv_dir / "Lib" / "site-packages" / "kimi_cli"
            if (candidate / TARGET_SLASH).exists():
                return candidate
    except Exception:
        pass

    # Strategy 3: follow the kimi binary (shutil.which is cross-platform)
    kimi_bin = shutil.which("kimi")
    if kimi_bin:
        pkg_base = Path(kimi_bin).resolve().parent.parent
        # Linux / macOS: bin/ sibling is lib/python3.x/site-packages/
        for candidate in (pkg_base / "lib").rglob("site-packages/kimi_cli"):
            if (candidate / TARGET_SLASH).exists():
                return candidate
        # Windows: Scripts/ sibling is Lib/site-packages/
        candidate = pkg_base / "Lib" / "site-packages" / "kimi_cli"
        if (candidate / TARGET_SLASH).exists():
            return candidate

    # Strategy 4: common hardcoded paths
    home = Path.home()
    candidates = [
        home / ".local/share/uv/tools/kimi-cli/lib/python3.13/site-packages/kimi_cli",
        home / ".local/share/uv/tools/kimi-cli/lib/python3.12/site-packages/kimi_cli",
        home / ".local/share/uv/tools/kimi-cli/lib/python3.11/site-packages/kimi_cli",
    ]
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(
            Path(appdata) / "uv" / "tools" / "kimi-cli" / "Lib" / "site-packages" / "kimi_cli"
        )
    for candidate in candidates:
        if (candidate / TARGET_SLASH).exists():
            return candidate

    return None


# ── Helpers ───────────────────────────────────────────────────────────────────

_SLASH_ANCHORS = ["editor", "changelog", "theme", "feedback", "clear", "new"]

def find_insertion_line(source: str) -> int | None:
    """Return the 1-based line before which the patch should be inserted.

    Tries each function name in _SLASH_ANCHORS in order — the first one found
    at module level is used as the insertion point. This handles version
    differences where a given function may not exist yet.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    funcs: dict[str, ast.AsyncFunctionDef | ast.FunctionDef] = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
    }
    for name in _SLASH_ANCHORS:
        node = funcs.get(name)
        if node is not None:
            if node.decorator_list:
                return node.decorator_list[0].lineno
            return node.lineno
    return None


def verify_syntax(path: Path) -> bool:
    try:
        ast.parse(path.read_text(encoding="utf-8"))
        return True
    except SyntaxError as exc:
        print(f"[ERROR] Syntax check failed: {exc}")
        return False


# ── Slash patch ───────────────────────────────────────────────────────────────

def is_patched_slash(target: Path) -> bool:
    return PATCH_MARKER_SLASH in target.read_text(encoding="utf-8")


def apply_slash(target: Path) -> bool:
    if is_patched_slash(target):
        print("[OK] slash.py already patched. Nothing to do.")
        return True

    original = target.read_text(encoding="utf-8")

    insert_at = find_insertion_line(original)
    if insert_at is None:
        print(
            "[ERROR] Could not find the 'editor' function in slash.py.\n"
            "        The file structure may have changed in this version of kimi-cli."
        )
        return False

    backup = target.with_suffix(".py.backup")
    shutil.copy2(target, backup)

    lines = original.splitlines(keepends=True)
    new_content = (
        "".join(lines[: insert_at - 1])
        + PATCH_CODE_SLASH
        + "".join(lines[insert_at - 1 :])
    )
    target.write_text(new_content, encoding="utf-8")

    if not verify_syntax(target):
        print("[ERROR] Patch broke syntax. Restoring backup...")
        shutil.copy2(backup, target)
        return False

    print("[OK] slash.py patched (/thinking command + /t alias).")
    print(f"[OK] Backup saved to: {backup}")
    return True


def restore_slash(target: Path) -> bool:
    backup = target.with_suffix(".py.backup")
    if not backup.exists():
        print("[ERROR] No backup found for slash.py. Cannot restore.")
        return False
    shutil.copy2(backup, target)
    print("[OK] slash.py restored from backup.")
    return True


# ── Prompt patch ──────────────────────────────────────────────────────────────

def is_patched_prompt(target: Path) -> bool:
    return PATCH_MARKER_PROMPT in target.read_text(encoding="utf-8")


def apply_prompt(target: Path) -> bool:
    if is_patched_prompt(target):
        print("[OK] prompt.py already patched. Nothing to do.")
        return True

    original = target.read_text(encoding="utf-8")

    if KB_ANCHOR not in original:
        print(
            "[ERROR] Could not find the Ctrl+X binding anchor in prompt.py.\n"
            "        The file structure may have changed in this version of kimi-cli."
        )
        return False
    if TIPS_ANCHOR not in original:
        print(
            "[ERROR] Could not find the toolbar tips anchor in prompt.py.\n"
            "        The file structure may have changed in this version of kimi-cli."
        )
        return False

    backup = target.with_suffix(".py.backup")
    shutil.copy2(target, backup)

    new_content = original.replace(KB_ANCHOR, PATCH_KB + KB_ANCHOR, 1)
    new_content = new_content.replace(TIPS_ANCHOR, TIPS_INSERT + TIPS_ANCHOR, 1)
    target.write_text(new_content, encoding="utf-8")

    if not verify_syntax(target):
        print("[ERROR] Patch broke syntax. Restoring backup...")
        shutil.copy2(backup, target)
        return False

    print("[OK] prompt.py patched (Ctrl+T key binding + toolbar tip).")
    print(f"[OK] Backup saved to: {backup}")
    return True


def restore_prompt(target: Path) -> bool:
    backup = target.with_suffix(".py.backup")
    if not backup.exists():
        print("[ERROR] No backup found for prompt.py. Cannot restore.")
        return False
    shutil.copy2(backup, target)
    print("[OK] prompt.py restored from backup.")
    return True


# ── Orchestration ─────────────────────────────────────────────────────────────

def apply(root: Path) -> bool:
    slash_ok = apply_slash(root / TARGET_SLASH)
    if not slash_ok:
        return False
    prompt_ok = apply_prompt(root / TARGET_PROMPT)
    if not prompt_ok:
        return False
    print("[INFO] Restart kimi-cli for /thinking and Ctrl+T to be available.")
    return True


def restore(root: Path) -> bool:
    ok = restore_slash(root / TARGET_SLASH)
    ok = restore_prompt(root / TARGET_PROMPT) and ok
    return ok


def status(root: Path) -> None:
    slash_target = root / TARGET_SLASH
    prompt_target = root / TARGET_PROMPT

    for label, target, is_patched_fn in (
        ("slash.py", slash_target, is_patched_slash),
        ("prompt.py", prompt_target, is_patched_prompt),
    ):
        patched = is_patched_fn(target)
        backup = target.with_suffix(".py.backup")
        print(f"[STATUS] {label}: {'patched' if patched else 'not patched'}")
        print(f"[STATUS] {label} target : {target}")
        print(f"[STATUS] {label} backup : {'exists' if backup.exists() else 'missing'}")


def main() -> int:
    parser = argparse.ArgumentParser(description="kimi-cli /thinking command installer")
    parser.add_argument("action", choices=["apply", "restore", "status"], help="Action to perform")
    args = parser.parse_args()

    root = find_kimi_cli_root()
    if root is None:
        print("[ERROR] Could not find kimi-cli installation.")
        print("        Make sure it is installed and importable.")
        return 1

    if args.action == "apply":
        return 0 if apply(root) else 1
    elif args.action == "restore":
        return 0 if restore(root) else 1
    else:
        status(root)
        return 0


if __name__ == "__main__":
    sys.exit(main())
