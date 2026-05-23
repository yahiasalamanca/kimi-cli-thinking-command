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

# ── Slash anchor functions (tried in order) ───────────────────────────────────

_SLASH_ANCHORS = ["editor", "changelog", "theme", "feedback", "clear", "new"]


# ── Package discovery ─────────────────────────────────────────────────────────

def _check_root(candidate: Path) -> Path | None:
    return candidate if (candidate / TARGET_SLASH).exists() else None


def find_kimi_cli_root() -> Path | None:
    """Locate the installed kimi_cli package root directory."""
    # Strategy 1: importlib — most reliable, works when running inside the same env
    spec = importlib.util.find_spec("kimi_cli")
    if spec is not None and spec.origin is not None:
        root = _check_root(Path(spec.origin).parent)
        if root:
            return root

    # Strategy 2: uv tool dir — respects UV_TOOL_DIR and all uv env overrides
    uv_dir: Path | None = None
    uv_tool_dir_env = os.environ.get("UV_TOOL_DIR")
    if uv_tool_dir_env:
        uv_dir = Path(uv_tool_dir_env) / "kimi-cli"
    else:
        try:
            result = subprocess.run(
                ["uv", "tool", "dir", "kimi-cli"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                uv_dir = Path(result.stdout.strip())
        except Exception:
            pass
    if uv_dir is not None:
        # Linux / macOS: lib/python3.x/site-packages/
        for candidate in uv_dir.glob("lib/python*/site-packages/kimi_cli"):
            root = _check_root(candidate)
            if root:
                return root
        # Windows: Lib/site-packages/ (no python-version subdirectory)
        root = _check_root(uv_dir / "Lib" / "site-packages" / "kimi_cli")
        if root:
            return root

    # Strategy 3: follow the kimi binary (shutil.which is cross-platform)
    kimi_bin = shutil.which("kimi")
    if kimi_bin:
        pkg_base = Path(kimi_bin).resolve().parent.parent
        for candidate in (pkg_base / "lib").rglob("site-packages/kimi_cli"):
            root = _check_root(candidate)
            if root:
                return root
        root = _check_root(pkg_base / "Lib" / "site-packages" / "kimi_cli")
        if root:
            return root

    # Strategy 4: hardcoded platform paths
    home = Path.home()
    python_versions = ["python3.13", "python3.12", "python3.11"]

    # Linux / macOS (XDG): respects $XDG_DATA_HOME, falls back to ~/.local/share
    xdg_data = os.environ.get("XDG_DATA_HOME")
    uv_data = Path(xdg_data) / "uv" if xdg_data else home / ".local" / "share" / "uv"
    for py in python_versions:
        root = _check_root(uv_data / "tools" / "kimi-cli" / "lib" / py / "site-packages" / "kimi_cli")
        if root:
            return root

    # macOS legacy path (pre-XDG uv installs used ~/Library/Application Support/uv)
    if sys.platform == "darwin":
        mac_legacy = home / "Library" / "Application Support" / "uv" / "tools" / "kimi-cli"
        for py in python_versions:
            root = _check_root(mac_legacy / "lib" / py / "site-packages" / "kimi_cli")
            if root:
                return root

    # Windows: %APPDATA%\uv\tools\kimi-cli\Lib\site-packages\kimi_cli
    appdata = os.environ.get("APPDATA")
    if appdata:
        root = _check_root(
            Path(appdata) / "uv" / "tools" / "kimi-cli" / "Lib" / "site-packages" / "kimi_cli"
        )
        if root:
            return root

    return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def find_insertion_line(source: str) -> int | None:
    """Return the 1-based line before which the patch should be inserted.

    Tries each function name in _SLASH_ANCHORS in order — the first one found
    at module level is used as the insertion point. Handles version differences
    where a given function may not exist yet.
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
            return node.decorator_list[0].lineno if node.decorator_list else node.lineno
    return None


def verify_syntax(source: str, label: str) -> bool:
    try:
        ast.parse(source)
        return True
    except SyntaxError as exc:
        print(f"[ERROR] Syntax check failed for {label}: {exc}")
        return False


# ── Slash patch ───────────────────────────────────────────────────────────────

def apply_slash(target: Path) -> bool:
    source = target.read_text(encoding="utf-8")

    if PATCH_MARKER_SLASH in source:
        print("[OK] slash.py already patched. Nothing to do.")
        return True

    insert_at = find_insertion_line(source)
    if insert_at is None:
        tried = ", ".join(_SLASH_ANCHORS)
        print(
            f"[ERROR] Could not find a suitable insertion anchor in slash.py.\n"
            f"        Tried: {tried}\n"
            f"        The file structure may have changed in this version of kimi-cli."
        )
        return False

    backup = target.with_suffix(".py.backup")
    shutil.copy2(target, backup)

    lines = source.splitlines(keepends=True)
    new_source = (
        "".join(lines[: insert_at - 1])
        + PATCH_CODE_SLASH
        + "".join(lines[insert_at - 1 :])
    )

    if not verify_syntax(new_source, "slash.py"):
        print("[ERROR] Patch produced invalid syntax. Backup left untouched.")
        return False

    target.write_text(new_source, encoding="utf-8")
    print("[OK] slash.py patched (/thinking command + /t alias).")
    print(f"[OK] Backup saved to: {backup}")
    return True


def restore_slash(target: Path) -> bool:
    backup = target.with_suffix(".py.backup")
    if not backup.exists():
        if PATCH_MARKER_SLASH not in target.read_text(encoding="utf-8"):
            print("[OK] slash.py was not patched. Nothing to restore.")
            return True
        print("[ERROR] No backup found for slash.py. Cannot restore.")
        return False
    shutil.copy2(backup, target)
    print("[OK] slash.py restored from backup.")
    return True


# ── Prompt patch ──────────────────────────────────────────────────────────────

def apply_prompt(target: Path) -> bool:
    source = target.read_text(encoding="utf-8")

    if PATCH_MARKER_PROMPT in source:
        print("[OK] prompt.py already patched. Nothing to do.")
        return True

    if KB_ANCHOR not in source:
        print(
            "[ERROR] Could not find the Ctrl+X binding anchor in prompt.py.\n"
            "        The file structure may have changed in this version of kimi-cli."
        )
        return False
    if TIPS_ANCHOR not in source:
        print(
            "[ERROR] Could not find the toolbar tips anchor in prompt.py.\n"
            "        The file structure may have changed in this version of kimi-cli."
        )
        return False

    backup = target.with_suffix(".py.backup")
    shutil.copy2(target, backup)

    new_source = source.replace(KB_ANCHOR, PATCH_KB + KB_ANCHOR, 1)
    new_source = new_source.replace(TIPS_ANCHOR, TIPS_INSERT + TIPS_ANCHOR, 1)

    if not verify_syntax(new_source, "prompt.py"):
        print("[ERROR] Patch produced invalid syntax. Backup left untouched.")
        return False

    target.write_text(new_source, encoding="utf-8")
    print("[OK] prompt.py patched (Ctrl+T key binding + toolbar tip).")
    print(f"[OK] Backup saved to: {backup}")
    return True


def restore_prompt(target: Path) -> bool:
    backup = target.with_suffix(".py.backup")
    if not backup.exists():
        if PATCH_MARKER_PROMPT not in target.read_text(encoding="utf-8"):
            print("[OK] prompt.py was not patched. Nothing to restore.")
            return True
        print("[ERROR] No backup found for prompt.py. Cannot restore.")
        return False
    shutil.copy2(backup, target)
    print("[OK] prompt.py restored from backup.")
    return True


# ── Orchestration ─────────────────────────────────────────────────────────────

def apply(root: Path) -> bool:
    if not apply_slash(root / TARGET_SLASH):
        return False
    if not apply_prompt(root / TARGET_PROMPT):
        return False
    print("[INFO] Restart kimi-cli for /thinking and Ctrl+T to be available.")
    return True


def restore(root: Path) -> bool:
    slash_ok = restore_slash(root / TARGET_SLASH)
    prompt_ok = restore_prompt(root / TARGET_PROMPT)
    return slash_ok and prompt_ok


def status(root: Path) -> None:
    for label, target, marker in (
        ("slash.py",  root / TARGET_SLASH,  PATCH_MARKER_SLASH),
        ("prompt.py", root / TARGET_PROMPT, PATCH_MARKER_PROMPT),
    ):
        patched = marker in target.read_text(encoding="utf-8")
        backup = target.with_suffix(".py.backup")
        print(f"[STATUS] {label}: {'patched' if patched else 'not patched'}")
        print(f"[STATUS] {label} target : {target}")
        print(f"[STATUS] {label} backup : {'exists' if backup.exists() else 'missing'}")


def main() -> int:
    parser = argparse.ArgumentParser(description="kimi-cli /thinking command installer")
    parser.add_argument("action", choices=["apply", "restore", "status"])
    args = parser.parse_args()

    root = find_kimi_cli_root()
    if root is None:
        print("[ERROR] Could not find kimi-cli installation.")
        print("        Make sure kimi-cli is installed (uv tool install kimi-cli).")
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
