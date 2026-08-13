#!/usr/bin/env python3
"""Install the durable red-team workflow, context hooks, and boundary skill for Claude Code."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


APP_NAME = "codex-redteam-claude-adapter"
APP_VERSION = "2.1.0"
MCP_SERVER_NAME = "claude-redteam-runtime"
BLOCK_START = "<!-- codex-redteam-claude:start -->"
BLOCK_END = "<!-- codex-redteam-claude:end -->"
HOOK_FILENAMES = ("session-start-context.py", "hook-security-context-hook.py")
REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_CLAUDE = REPO_ROOT / "claude"
SOURCE_CODEX = REPO_ROOT / "codex"


@dataclass(frozen=True)
class InstallPaths:
    scope: str
    project_home: Path | None
    claude_home: Path
    settings_file: Path
    mcp_file: Path
    claude_md: Path
    adapter_root: Path
    hooks_dir: Path
    runtime_dir: Path
    workflows_dir: Path
    config_file: Path
    operations_dir: Path
    command_file: Path
    skill_dir: Path
    manifest_file: Path


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="backslashreplace")


def normalize(value: str | Path) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def resolve_paths(args: argparse.Namespace) -> InstallPaths:
    if args.project_home:
        project = normalize(args.project_home)
        claude_home = project / ".claude"
        return InstallPaths(
            scope="project",
            project_home=project,
            claude_home=claude_home,
            settings_file=claude_home / "settings.json",
            mcp_file=project / ".mcp.json",
            claude_md=project / "CLAUDE.md",
            adapter_root=claude_home / "redteam-mode",
            hooks_dir=claude_home / "redteam-mode" / "hooks",
            runtime_dir=claude_home / "redteam-mode" / "runtime",
            workflows_dir=claude_home / "redteam-mode" / "workflows",
            config_file=claude_home / "redteam-mode" / "config.toml",
            operations_dir=claude_home / "redteam-mode" / "operations",
            command_file=claude_home / "commands" / "redteam.md",
            skill_dir=claude_home / "skills" / "redteam-boundary-policy",
            manifest_file=claude_home / "redteam-install-manifest.json",
        )
    explicit = args.claude_home or os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude")
    claude_home = normalize(explicit)
    mcp_file = normalize(args.claude_config) if args.claude_config else claude_home.parent / ".claude.json"
    return InstallPaths(
        scope="user",
        project_home=None,
        claude_home=claude_home,
        settings_file=claude_home / "settings.json",
        mcp_file=mcp_file,
        claude_md=claude_home / "CLAUDE.md",
        adapter_root=claude_home / "redteam-mode",
        hooks_dir=claude_home / "redteam-mode" / "hooks",
        runtime_dir=claude_home / "redteam-mode" / "runtime",
        workflows_dir=claude_home / "redteam-mode" / "workflows",
        config_file=claude_home / "redteam-mode" / "config.toml",
        operations_dir=claude_home / "redteam-mode" / "operations",
        command_file=claude_home / "commands" / "redteam.md",
        skill_dir=claude_home / "skills" / "redteam-boundary-policy",
        manifest_file=claude_home / "redteam-install-manifest.json",
    )


def load_json(path: Path, *, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.is_file():
        return dict(default or {})
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def write_text(path: Path, content: str, *, dry_run: bool) -> None:
    print(f"write {path}")
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp-codex-redteam")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def write_json(path: Path, value: dict[str, Any], *, dry_run: bool) -> None:
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n", dry_run=dry_run)


def backup_if_changed(path: Path, new_content: str, *, dry_run: bool) -> None:
    if not path.is_file():
        return
    try:
        current = path.read_text(encoding="utf-8-sig")
    except OSError:
        return
    if current == new_content:
        return
    backup = path.with_name(f"{path.name}.codex-redteam-{datetime.now().strftime('%Y%m%d%H%M%S%f')}.bak")
    print(f"backup {path} -> {backup}")
    if not dry_run:
        shutil.copy2(path, backup)


def _is_within(path: Path, root: Path) -> bool:
    resolved = path.resolve(strict=False)
    boundary = root.resolve(strict=False)
    return resolved == boundary or str(resolved).casefold().startswith(str(boundary).casefold() + os.sep)


def remove_path(path: Path, *, root: Path, dry_run: bool) -> None:
    if not path.exists():
        return
    if not _is_within(path, root):
        raise ValueError(f"managed path escapes Claude home: {path}")
    print(f"remove {path}")
    if dry_run:
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def copy_tree(source: Path, destination: Path, *, dry_run: bool) -> None:
    print(f"copy {source} -> {destination}")
    if dry_run:
        return
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def copy_file(source: Path, destination: Path, *, dry_run: bool) -> None:
    print(f"copy {source} -> {destination}")
    if dry_run:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def hook_command(path: Path) -> str:
    python = Path(sys.executable).resolve().as_posix()
    script = path.resolve(strict=False).as_posix()
    return f'"{python}" -B "{script}"'


def _is_managed_hook(item: object) -> bool:
    if not isinstance(item, dict):
        return False
    command = str(item.get("command") or "").casefold()
    return any(filename.casefold() in command and "redteam-mode" in command for filename in HOOK_FILENAMES)


def merge_hook_event(settings: dict[str, Any], event: str, command: str, status: str) -> None:
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}
        settings["hooks"] = hooks
    groups = hooks.get(event)
    if not isinstance(groups, list):
        groups = []
    cleaned: list[dict[str, Any]] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        entries = group.get("hooks")
        if not isinstance(entries, list):
            cleaned.append(group)
            continue
        kept = [entry for entry in entries if not _is_managed_hook(entry)]
        if kept:
            updated = dict(group)
            updated["hooks"] = kept
            cleaned.append(updated)
    managed = {
        "hooks": [
            {
                "type": "command",
                "command": command,
                "timeout": 15,
                "statusMessage": status,
            }
        ]
    }
    hooks[event] = [managed, *cleaned]


def merge_settings(paths: InstallPaths) -> dict[str, Any]:
    settings = load_json(paths.settings_file)
    merge_hook_event(
        settings,
        "SessionStart",
        hook_command(paths.hooks_dir / "session-start-context.py"),
        "Loading durable red-team session context",
    )
    merge_hook_event(
        settings,
        "UserPromptSubmit",
        hook_command(paths.hooks_dir / "hook-security-context-hook.py"),
        "Dispatching durable red-team workflow",
    )
    return settings


def mcp_definition(paths: InstallPaths) -> dict[str, Any]:
    return {
        "type": "stdio",
        "command": str(Path(sys.executable).resolve()),
        "args": [
            "-B",
            "-m",
            "runtime.mcp_server",
            "--root",
            str(paths.operations_dir.resolve(strict=False)),
            "--config",
            str(paths.config_file.resolve(strict=False)),
        ],
        "env": {
            "PYTHONPATH": str(paths.adapter_root.resolve(strict=False)),
            "CODEX_HOME": str(paths.claude_home.resolve(strict=False)),
            "CODEX_REDTEAM_CONFIG": str(paths.config_file.resolve(strict=False)),
            "CLAUDE_REDTEAM_HOME": str(paths.claude_home.resolve(strict=False)),
            "CLAUDE_REDTEAM_CONFIG": str(paths.config_file.resolve(strict=False)),
        },
    }


def merge_mcp(paths: InstallPaths) -> tuple[dict[str, Any], dict[str, Any]]:
    config = load_json(paths.mcp_file)
    servers = config.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        servers = {}
        config["mcpServers"] = servers
    definition = mcp_definition(paths)
    servers[MCP_SERVER_NAME] = definition
    return config, definition


def merge_claude_md(path: Path) -> str:
    fragment = (SOURCE_CLAUDE / "CLAUDE.md.fragment").read_text(encoding="utf-8-sig").strip()
    current = path.read_text(encoding="utf-8-sig") if path.is_file() else ""
    pattern = re.compile(re.escape(BLOCK_START) + r".*?" + re.escape(BLOCK_END), re.DOTALL)
    if pattern.search(current):
        return pattern.sub(fragment, current).rstrip() + "\n"
    separator = "\n\n" if current.strip() else ""
    return current.rstrip() + separator + fragment + "\n"


def remove_claude_md_block(path: Path) -> str | None:
    if not path.is_file():
        return None
    current = path.read_text(encoding="utf-8-sig")
    pattern = re.compile(r"\n*" + re.escape(BLOCK_START) + r".*?" + re.escape(BLOCK_END) + r"\n*", re.DOTALL)
    rendered = pattern.sub("\n", current).strip()
    return rendered + "\n" if rendered else ""


def validate_sources() -> None:
    required = [
        SOURCE_CLAUDE / "config.toml",
        SOURCE_CLAUDE / "CLAUDE.md.fragment",
        SOURCE_CLAUDE / "hooks" / "session-start-context.py",
        SOURCE_CLAUDE / "hooks" / "hook-security-context-hook.py",
        SOURCE_CLAUDE / "commands" / "redteam.md",
        SOURCE_CODEX / "runtime" / "mcp_server.py",
        SOURCE_CODEX / "workflows" / "generic-adaptive.toml",
        REPO_ROOT / "agents" / "skills" / "redteam-boundary-policy" / "SKILL.md",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing Claude adapter sources: " + ", ".join(missing))


def existing_manifest(paths: InstallPaths) -> dict[str, Any]:
    return load_json(paths.manifest_file) if paths.manifest_file.is_file() else {}


def install(paths: InstallPaths, *, dry_run: bool) -> None:
    validate_sources()
    manifest = existing_manifest(paths)
    if paths.adapter_root.exists() and manifest.get("app") != APP_NAME:
        raise ValueError(f"adapter directory already exists without a matching manifest: {paths.adapter_root}")
    if paths.skill_dir.exists() and manifest.get("app") != APP_NAME:
        raise ValueError(f"skill directory already exists without a matching manifest: {paths.skill_dir}")
    if paths.command_file.exists() and manifest.get("app") != APP_NAME:
        raise ValueError(f"command file already exists without a matching manifest: {paths.command_file}")

    settings = merge_settings(paths)
    mcp_config, definition = merge_mcp(paths)
    claude_md = merge_claude_md(paths.claude_md)
    settings_text = json.dumps(settings, ensure_ascii=False, indent=2) + "\n"
    mcp_text = json.dumps(mcp_config, ensure_ascii=False, indent=2) + "\n"

    backup_if_changed(paths.settings_file, settings_text, dry_run=dry_run)
    backup_if_changed(paths.mcp_file, mcp_text, dry_run=dry_run)
    backup_if_changed(paths.claude_md, claude_md, dry_run=dry_run)

    copy_tree(SOURCE_CLAUDE / "hooks", paths.hooks_dir, dry_run=dry_run)
    copy_tree(SOURCE_CODEX / "runtime", paths.runtime_dir, dry_run=dry_run)
    if not dry_run:
        paths.workflows_dir.mkdir(parents=True, exist_ok=True)
    copy_file(SOURCE_CODEX / "workflows" / "generic-adaptive.toml", paths.workflows_dir / "generic-adaptive.toml", dry_run=dry_run)
    copy_file(SOURCE_CLAUDE / "config.toml", paths.config_file, dry_run=dry_run)
    copy_file(SOURCE_CLAUDE / "commands" / "redteam.md", paths.command_file, dry_run=dry_run)
    copy_tree(REPO_ROOT / "agents" / "skills" / "redteam-boundary-policy", paths.skill_dir, dry_run=dry_run)
    write_text(paths.settings_file, settings_text, dry_run=dry_run)
    write_text(paths.mcp_file, mcp_text, dry_run=dry_run)
    write_text(paths.claude_md, claude_md, dry_run=dry_run)

    manifest_data = {
        "app": APP_NAME,
        "version": APP_VERSION,
        "scope": paths.scope,
        "project_home": str(paths.project_home) if paths.project_home else None,
        "claude_home": str(paths.claude_home),
        "settings_file": str(paths.settings_file),
        "mcp_file": str(paths.mcp_file),
        "claude_md": str(paths.claude_md),
        "adapter_root": str(paths.adapter_root),
        "command_file": str(paths.command_file),
        "skill_dir": str(paths.skill_dir),
        "mcp_server": {"name": MCP_SERVER_NAME, "definition": definition},
    }
    write_json(paths.manifest_file, manifest_data, dry_run=dry_run)
    print(f"Claude Code adapter installed ({paths.scope} scope): {paths.claude_home}")


def _remove_managed_hooks(settings: dict[str, Any]) -> dict[str, Any]:
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return settings
    for event in ("SessionStart", "UserPromptSubmit"):
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        cleaned = []
        for group in groups:
            if not isinstance(group, dict):
                continue
            entries = group.get("hooks")
            if not isinstance(entries, list):
                cleaned.append(group)
                continue
            kept = [entry for entry in entries if not _is_managed_hook(entry)]
            if kept:
                updated = dict(group)
                updated["hooks"] = kept
                cleaned.append(updated)
        if cleaned:
            hooks[event] = cleaned
        else:
            hooks.pop(event, None)
    if not hooks:
        settings.pop("hooks", None)
    return settings


def uninstall(paths: InstallPaths, *, dry_run: bool) -> None:
    manifest = existing_manifest(paths)
    if manifest.get("app") != APP_NAME:
        raise ValueError(f"matching Claude adapter manifest not found: {paths.manifest_file}")

    settings = _remove_managed_hooks(load_json(paths.settings_file))
    settings_text = json.dumps(settings, ensure_ascii=False, indent=2) + "\n"
    backup_if_changed(paths.settings_file, settings_text, dry_run=dry_run)
    write_text(paths.settings_file, settings_text, dry_run=dry_run)

    mcp_config = load_json(paths.mcp_file)
    servers = mcp_config.get("mcpServers")
    if isinstance(servers, dict):
        expected = manifest.get("mcp_server", {}).get("definition") if isinstance(manifest.get("mcp_server"), dict) else None
        if expected is None or servers.get(MCP_SERVER_NAME) == expected:
            servers.pop(MCP_SERVER_NAME, None)
        if not servers:
            mcp_config.pop("mcpServers", None)
    mcp_text = json.dumps(mcp_config, ensure_ascii=False, indent=2) + "\n"
    backup_if_changed(paths.mcp_file, mcp_text, dry_run=dry_run)
    write_text(paths.mcp_file, mcp_text, dry_run=dry_run)

    rendered = remove_claude_md_block(paths.claude_md)
    if rendered is not None:
        backup_if_changed(paths.claude_md, rendered, dry_run=dry_run)
        write_text(paths.claude_md, rendered, dry_run=dry_run)

    remove_path(paths.adapter_root, root=paths.claude_home, dry_run=dry_run)
    remove_path(paths.command_file, root=paths.claude_home, dry_run=dry_run)
    remove_path(paths.skill_dir, root=paths.claude_home, dry_run=dry_run)
    remove_path(paths.manifest_file, root=paths.claude_home, dry_run=dry_run)
    print(f"Claude Code adapter uninstalled: {paths.claude_home}")


def _run_hook(path: Path, payload: dict[str, Any], env: dict[str, str]) -> dict[str, Any] | None:
    completed = subprocess.run(
        [sys.executable, "-B", str(path)],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        env=env,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"hook failed: {path}: {completed.stderr.strip()}")
    output = completed.stdout.strip()
    return json.loads(output) if output else None


def validate(paths: InstallPaths, *, smoke: bool = True) -> list[str]:
    messages: list[str] = []
    required = [
        paths.hooks_dir / "session-start-context.py",
        paths.hooks_dir / "hook-security-context-hook.py",
        paths.hooks_dir / "redteam_state.py",
        paths.runtime_dir / "mcp_server.py",
        paths.workflows_dir / "generic-adaptive.toml",
        paths.config_file,
        paths.command_file,
        paths.skill_dir / "SKILL.md",
        paths.manifest_file,
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError("missing installed files: " + ", ".join(missing))
    messages.append("required workflow, hook, runtime, command, and skill files present")

    settings = load_json(paths.settings_file)
    settings_blob = json.dumps(settings, ensure_ascii=False)
    if not all(filename in settings_blob for filename in HOOK_FILENAMES):
        raise RuntimeError("Claude settings do not reference both context hooks")
    messages.append("SessionStart and UserPromptSubmit hooks registered")

    mcp_config = load_json(paths.mcp_file)
    definition = mcp_config.get("mcpServers", {}).get(MCP_SERVER_NAME) if isinstance(mcp_config.get("mcpServers"), dict) else None
    if not isinstance(definition, dict):
        raise RuntimeError("Claude MCP runtime registration missing")
    messages.append("Claude MCP runtime registered")

    skill_text = (paths.skill_dir / "SKILL.md").read_text(encoding="utf-8-sig")
    if "name: redteam-boundary-policy" not in skill_text:
        raise RuntimeError("Claude skill metadata invalid")
    messages.append("redteam-boundary-policy skill valid")

    if smoke:
        with tempfile.TemporaryDirectory(prefix="claude-redteam-validate-") as temporary:
            env = dict(os.environ)
            env.update({
                "CLAUDE_REDTEAM_HOME": temporary,
                "CLAUDE_REDTEAM_CONFIG": str(paths.config_file),
                "PYTHONPATH": str(paths.adapter_root),
            })
            session_id = "claude-adapter-validation"
            session = _run_hook(paths.hooks_dir / "session-start-context.py", {"session_id": session_id, "source": "startup"}, env)
            if not isinstance(session, dict) or "additionalContext" not in session.get("hookSpecificOutput", {}):
                raise RuntimeError("SessionStart hook did not emit additionalContext")
            _run_hook(paths.hooks_dir / "hook-security-context-hook.py", {"session_id": session_id, "prompt": "/redteam on"}, env)
            turn = _run_hook(
                paths.hooks_dir / "hook-security-context-hook.py",
                {"session_id": session_id, "prompt": "validate SQL injection on https://target.invalid and produce a PoC report"},
                env,
            )
            context = turn.get("hookSpecificOutput", {}).get("additionalContext", "") if isinstance(turn, dict) else ""
            if "redteam_run" not in context or "generic-adaptive" not in context:
                raise RuntimeError("UserPromptSubmit hook did not dispatch the durable workflow")
        messages.append("context injection smoke test passed")

        request_lines = [
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
        ]
        env = dict(os.environ)
        env.update({str(key): str(value) for key, value in definition.get("env", {}).items()})
        with tempfile.TemporaryDirectory(prefix="claude-redteam-mcp-") as temporary:
            command = [str(definition["command"]), *[str(item) for item in definition.get("args", [])]]
            root_index = command.index("--root") + 1
            command[root_index] = temporary
            completed = subprocess.run(
                command,
                input="\n".join(request_lines) + "\n",
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=25,
                env=env,
                check=False,
            )
        responses = []
        for line in completed.stdout.splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                responses.append(value)
        tools_response = next((item for item in responses if item.get("id") == 2), {})
        tools = tools_response.get("result", {}).get("tools", []) if isinstance(tools_response.get("result"), dict) else []
        if completed.returncode != 0 or not any(item.get("name") == "redteam_run" for item in tools if isinstance(item, dict)):
            raise RuntimeError(f"MCP runtime smoke test failed: {completed.stderr.strip()}")
        messages.append("MCP redteam_run discovery smoke test passed")
    return messages


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-home", help="Install into PROJECT/.claude and PROJECT/.mcp.json")
    parser.add_argument("--claude-home", help="User Claude config directory; defaults to CLAUDE_CONFIG_DIR or ~/.claude")
    parser.add_argument("--claude-config", help="User-scope Claude state JSON; defaults to ~/.claude.json")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--uninstall", action="store_true")
    action.add_argument("--validate", action="store_true")
    parser.add_argument("--no-smoke", action="store_true", help="Skip hook and MCP subprocess smoke tests during validation")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    args = build_parser().parse_args(argv)
    if args.project_home and (args.claude_home or args.claude_config):
        raise SystemExit("--project-home cannot be combined with --claude-home or --claude-config")
    paths = resolve_paths(args)
    try:
        if args.uninstall:
            uninstall(paths, dry_run=args.dry_run)
        elif args.validate:
            for message in validate(paths, smoke=not args.no_smoke):
                print(f"OK: {message}")
        else:
            install(paths, dry_run=args.dry_run)
            if not args.dry_run:
                for message in validate(paths, smoke=False):
                    print(f"OK: {message}")
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
