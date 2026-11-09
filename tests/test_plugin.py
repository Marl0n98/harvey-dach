"""DACH: the Claude Code plugin ships the same check agents as the repo's
.claude folder, and its manifests point at the right places."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_plugin_agents_match_the_repo_agents():
    for name in ("zuordnungs-pruefer.md", "gegen-pruefer.md"):
        assert (ROOT / "plugin" / "agents" / name).read_text(encoding="utf-8") == \
            (ROOT / ".claude" / "agents" / name).read_text(encoding="utf-8")


def test_marketplace_points_at_the_plugin_folder():
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    [entry] = market["plugins"]
    plugin = json.loads((ROOT / entry["source"] / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert entry["name"] == plugin["name"] == "harvey-dach"
    assert (ROOT / entry["source"] / "skills" / "harvey" / "SKILL.md").exists()
    assert (ROOT / entry["source"] / "commands" / "leads-pruefen.md").exists()
