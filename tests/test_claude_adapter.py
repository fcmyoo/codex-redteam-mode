from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER_PATH = REPO_ROOT / "scripts" / "install_claude.py"


def load_installer():
    spec = importlib.util.spec_from_file_location("install_claude_test_module", INSTALLER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def installer():
    return load_installer()


def test_project_install_validate_and_uninstall_preserves_user_config(tmp_path: Path, installer) -> None:
    project = tmp_path / "project"
    claude_home = project / ".claude"
    claude_home.mkdir(parents=True)
    settings_path = claude_home / "settings.json"
    mcp_path = project / ".mcp.json"
    claude_md = project / "CLAUDE.md"
    settings_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {"hooks": [{"type": "command", "command": "user-hook", "timeout": 5}]}
                    ]
                },
                "permissions": {"allow": ["Bash(git status)"]},
            }
        ),
        encoding="utf-8",
    )
    mcp_path.write_text(
        json.dumps({"mcpServers": {"existing": {"type": "stdio", "command": "existing", "args": []}}}),
        encoding="utf-8",
    )
    claude_md.write_text("# User instructions\n", encoding="utf-8")

    args = installer.build_parser().parse_args(["--project-home", str(project)])
    paths = installer.resolve_paths(args)
    installer.install(paths, dry_run=False)
    installer.install(paths, dry_run=False)

    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    settings_blob = json.dumps(settings)
    assert settings_blob.count("session-start-context.py") == 1
    assert settings_blob.count("hook-security-context-hook.py") == 1
    assert "user-hook" in settings_blob
    mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
    assert mcp["mcpServers"]["existing"]["command"] == "existing"
    assert installer.MCP_SERVER_NAME in mcp["mcpServers"]
    assert (paths.runtime_dir / "mcp_server.py").is_file()
    assert (paths.workflows_dir / "generic-adaptive.toml").is_file()
    assert (paths.skill_dir / "SKILL.md").is_file()
    assert paths.command_file.read_text(encoding="utf-8").strip().endswith("/redteam $ARGUMENTS")
    assert claude_md.read_text(encoding="utf-8").count(installer.BLOCK_START) == 1

    messages = installer.validate(paths, smoke=True)
    assert "context injection smoke test passed" in messages
    assert "MCP redteam_run discovery smoke test passed" in messages

    installer.uninstall(paths, dry_run=False)
    restored_settings = json.loads(settings_path.read_text(encoding="utf-8"))
    restored_mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
    assert "user-hook" in json.dumps(restored_settings)
    assert all(filename not in json.dumps(restored_settings) for filename in installer.HOOK_FILENAMES)
    assert restored_mcp["mcpServers"]["existing"]["command"] == "existing"
    assert installer.MCP_SERVER_NAME not in restored_mcp["mcpServers"]
    assert claude_md.read_text(encoding="utf-8").strip() == "# User instructions"
    assert not paths.adapter_root.exists()
    assert not paths.skill_dir.exists()
    assert not paths.command_file.exists()


def test_user_scope_uses_top_level_claude_mcp_config(tmp_path: Path, installer) -> None:
    claude_home = tmp_path / ".claude"
    claude_config = tmp_path / ".claude.json"
    args = installer.build_parser().parse_args(
        ["--claude-home", str(claude_home), "--claude-config", str(claude_config)]
    )
    paths = installer.resolve_paths(args)
    installer.install(paths, dry_run=False)

    assert paths.scope == "user"
    assert paths.mcp_file == claude_config
    mcp = json.loads(claude_config.read_text(encoding="utf-8"))
    definition = mcp["mcpServers"][installer.MCP_SERVER_NAME]
    assert definition["env"]["CLAUDE_REDTEAM_HOME"] == str(claude_home.resolve())
    assert definition["env"]["PYTHONPATH"] == str(paths.adapter_root.resolve())
    assert paths.settings_file.is_file()
    assert (claude_home / "CLAUDE.md").is_file()


def test_install_rejects_unmanaged_destination(tmp_path: Path, installer) -> None:
    project = tmp_path / "project"
    unmanaged = project / ".claude" / "redteam-mode"
    unmanaged.mkdir(parents=True)
    (unmanaged / "user.txt").write_text("keep", encoding="utf-8")
    args = installer.build_parser().parse_args(["--project-home", str(project)])
    paths = installer.resolve_paths(args)

    with pytest.raises(ValueError, match="without a matching manifest"):
        installer.install(paths, dry_run=False)
    assert (unmanaged / "user.txt").read_text(encoding="utf-8") == "keep"
