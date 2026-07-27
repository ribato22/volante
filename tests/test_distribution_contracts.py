from __future__ import annotations

import json
import os
import re
import tomllib
from pathlib import Path

import pytest

from volante import __version__
from volante.bootstrap import NoProvidersConfiguredError, build_providers_from_env

ROOT = Path(__file__).resolve().parents[1]
PINNED_MCP_PACKAGE = f"volante[mcp]=={__version__}"

FAMILY_METADATA_SUFFIXES = {
    "MODELS",
    "NAMES",
    "STRENGTHS",
    "CONTEXT",
    "MAX_OUTPUT",
    "TOOLS",
    "COST_IN",
    "COST_OUT",
    "TIER",
}
COMPAT_METADATA_SUFFIXES = {
    "BASE_URL",
    "KEY",
    "MODEL",
    "NAME",
    "STRENGTHS",
    "CONTEXT",
    "MAX_OUTPUT",
    "TOOLS",
    "COST_IN",
    "COST_OUT",
    "TIER",
}
EXPECTED_ENV = (
    {"ANTHROPIC_API_KEY"}
    | {f"ANTHROPIC_{suffix}" for suffix in FAMILY_METADATA_SUFFIXES}
    | {"MOONSHOT_API_KEY", "MOONSHOT_BASE_URL"}
    | {f"MOONSHOT_{suffix}" for suffix in FAMILY_METADATA_SUFFIXES}
    | {"OLLAMA_API_KEY", "OLLAMA_BASE_URL"}
    | {f"OLLAMA_{suffix}" for suffix in FAMILY_METADATA_SUFFIXES}
    | {
        "CLAUDE_CODE_ENABLED",
        "CLAUDE_CODE_MODELS",
        "CLAUDE_CODE_TIER",
        "CLAUDE_CODE_MAX_OUTPUT",
        "CLAUDE_CODE_TIMEOUT",
        "CLAUDE_CODE_SYSTEM_PROMPT_MODE",
    }
    | {
        "CODEX_ENABLED",
        "CODEX_MODELS",
        "CODEX_TIER",
        "CODEX_CONTEXT",
        "CODEX_MAX_OUTPUT",
        "CODEX_COST_IN",
        "CODEX_COST_OUT",
        "CODEX_TIMEOUT",
    }
    | {
        f"{prefix}_{suffix}"
        for prefix in ("OPENAI_COMPAT", "OPENAI_COMPAT_2", "OPENAI_COMPAT_3")
        for suffix in COMPAT_METADATA_SUFFIXES
    }
    | {
        "VOLANTE_QUALITY_PROFILES_FILE",
        "VOLANTE_MODEL_OVERRIDES_FILE",
        "VOLANTE_FETCH_ALLOWLIST",
        "VOLANTE_READ_ROOT",
        "VOLANTE_SANDBOX",
        "VOLANTE_MAX_SUBSCRIPTION_CALLS",
    }
)
SECRET_ENV = {
    "ANTHROPIC_API_KEY",
    "MOONSHOT_API_KEY",
    "OLLAMA_API_KEY",
    "OPENAI_COMPAT_KEY",
    "OPENAI_COMPAT_2_KEY",
    "OPENAI_COMPAT_3_KEY",
}
CONFIG_REF = re.compile(r"\$\{user_config\.([a-z0-9_]+)\}")


def _json(path: str) -> dict:
    return json.loads((ROOT / path).read_text())


def _smithery_property(env_name: str) -> str:
    parts = env_name.split("_")
    if parts[0] == "VOLANTE":
        parts = parts[1:]
    return parts[0].lower() + "".join(part.title() for part in parts[1:])


def _render_mcpb_env(manifest: dict, overrides: dict[str, str]) -> dict[str, str]:
    config = {name: str(spec.get("default", "")) for name, spec in manifest["user_config"].items()}
    config.update(overrides)
    rendered: dict[str, str] = {}
    for env_name, template in manifest["server"]["mcp_config"]["env"].items():
        match = CONFIG_REF.fullmatch(template)
        assert match is not None
        rendered[env_name] = config[match.group(1)]
    return rendered


def _replace_provider_environment(monkeypatch: pytest.MonkeyPatch, values: dict[str, str]) -> None:
    prefixes = (
        "ANTHROPIC_",
        "MOONSHOT_",
        "OLLAMA_",
        "CLAUDE_CODE_",
        "CODEX_",
        "OPENAI_COMPAT",
        "VOLANTE_",
    )
    for name in list(os.environ):
        if name.startswith(prefixes):
            monkeypatch.delenv(name)
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_all_distribution_versions_match_package() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    server = _json("server.json")
    versions = {
        "server": server["version"],
        "server package": server["packages"][0]["version"],
        "MCPB": _json("mcpb/manifest.json")["version"],
        "Claude Code plugin": _json("plugins/volante/.claude-plugin/plugin.json")["version"],
    }
    assert pyproject["project"]["dynamic"] == ["version"]
    assert pyproject["tool"]["hatch"]["version"]["path"] == "src/volante/__init__.py"
    assert versions == {name: __version__ for name in versions}


def test_mcpb_exposes_the_complete_inventory_contract() -> None:
    manifest = _json("mcpb/manifest.json")
    env = manifest["server"]["mcp_config"]["env"]
    config = manifest["user_config"]

    assert set(env) == EXPECTED_ENV
    referenced_config: set[str] = set()
    for env_name, value in env.items():
        match = CONFIG_REF.fullmatch(value)
        assert match is not None, env_name
        referenced_config.add(match.group(1))
    assert referenced_config == set(config)

    for env_name, value in env.items():
        config_name = CONFIG_REF.fullmatch(value).group(1)  # type: ignore[union-attr]
        if env_name in SECRET_ENV:
            assert config[config_name]["sensitive"] is True
        else:
            assert config[config_name].get("sensitive", False) is False

    assert config["claude_code_enabled"]["default"] == "0"
    assert config["codex_enabled"]["default"] == "0"
    assert config["max_subscription_calls"]["default"] == "16"
    assert config["quality_profiles_file"]["type"] == "file"
    assert config["model_overrides_file"]["type"] == "file"
    assert config["read_root"]["type"] == "directory"
    assert "OPENAI_COMPAT_API_KEY" not in env


def test_mcpb_pristine_and_user_key_only_config_bootstrap_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _json("mcpb/manifest.json")
    pristine = _render_mcpb_env(manifest, {})
    assert set(pristine) == EXPECTED_ENV

    _replace_provider_environment(monkeypatch, pristine)
    with pytest.raises(NoProvidersConfiguredError):
        build_providers_from_env()

    key_only = _render_mcpb_env(manifest, {"anthropic_api_key": "test-anthropic-key"})
    _replace_provider_environment(monkeypatch, key_only)
    registry, providers, baseline = build_providers_from_env()
    assert set(providers) == {"anthropic/claude-opus-4-8"}
    assert registry.get("anthropic/claude-opus-4-8").tier == 4
    assert baseline == "anthropic/claude-opus-4-8"


def test_smithery_exposes_the_complete_inventory_contract() -> None:
    text = (ROOT / "smithery.yaml").read_text()
    command = text.split("  commandFunction:", 1)[1].split("  exampleConfig:", 1)[0]

    for env_name in EXPECTED_ENV:
        assert f"{env_name}:" in command
        property_name = _smithery_property(env_name)
        assert re.search(rf"^      {property_name}:", text, re.MULTILINE)

    for env_name in SECRET_ENV:
        property_name = _smithery_property(env_name)
        assert re.search(
            rf"^      {property_name}: "
            r"\{ type: string, format: password, writeOnly: true,",
            text,
            re.MULTILINE,
        )

    assert "OPENAI_COMPAT_API_KEY" not in text
    assert "CLAUDE_CODE_ENABLED: config.claudeCodeEnabled ? '1' : '0'" in command
    assert "CODEX_ENABLED: config.codexEnabled ? '1' : '0'" in command
    assert "String(config.claudeCodeEnabled" not in command
    assert "String(config.codexEnabled" not in command
    assert f"args: ['--from', '{PINNED_MCP_PACKAGE}', 'volante-mcp']" in command


def test_official_registry_exposes_the_complete_inventory_contract() -> None:
    server = _json("server.json")
    variables = {item["name"]: item for item in server["packages"][0]["environmentVariables"]}
    assert set(variables) == EXPECTED_ENV
    assert all(item["isRequired"] is False for item in variables.values())
    for name, item in variables.items():
        assert item["isSecret"] is (name in SECRET_ENV)
    assert "OPENAI_COMPAT_API_KEY" not in variables


def test_every_distribution_launch_pins_the_shipped_mcp_entrypoint() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    expected_args = ["--from", PINNED_MCP_PACKAGE, "volante-mcp"]
    mcpb = _json("mcpb/manifest.json")["server"]["mcp_config"]
    plugin = _json("plugins/volante/.mcp.json")["mcpServers"]["volante"]
    server_package = _json("server.json")["packages"][0]

    assert pyproject["project"]["scripts"]["volante-mcp"] == "volante_mcp:main"
    assert mcpb["command"] == plugin["command"] == "uvx"
    assert mcpb["args"] == plugin["args"] == expected_args
    assert server_package["runtimeArguments"] == [
        {"type": "named", "name": "--from", "value": PINNED_MCP_PACKAGE},
        {"type": "positional", "value": "volante-mcp"},
    ]
    assert PINNED_MCP_PACKAGE in (ROOT / "plugins/volante/README.md").read_text()
    assert PINNED_MCP_PACKAGE in (ROOT / "mcpb/README.md").read_text()

    marketplace = _json(".claude-plugin/marketplace.json")
    assert marketplace["plugins"][0]["source"] == "./plugins/volante"


def test_distribution_surfaces_describe_evidence_based_routing_without_overclaim() -> None:
    paths = (
        "server.json",
        "mcpb/manifest.json",
        "plugins/volante/commands/run.md",
        "plugins/volante/skills/orchestrate/SKILL.md",
    )
    required = " ".join(
        (
            "best predicted fit from configured metadata/evidence",
            "after hard-capability filtering",
        )
    )
    for path in paths:
        text = (ROOT / path).read_text()
        normalized = " ".join(text.split())
        assert required in normalized
        assert "best capable model" not in normalized.lower()
        assert "best-quality capable model" not in normalized.lower()


def test_external_github_actions_are_pinned_to_full_commit_shas() -> None:
    for workflow in sorted((ROOT / ".github/workflows").glob("*.yml")):
        for line in workflow.read_text().splitlines():
            match = re.search(r"\buses:\s*([^\s#]+)", line)
            if match is None or match.group(1).startswith("./"):
                continue
            assert re.search(r"@[0-9a-f]{40}$", match.group(1)), (
                f"{workflow.name}: external action is not commit-SHA pinned: {match.group(1)}"
            )


def test_mcp_publisher_binary_is_version_and_checksum_pinned() -> None:
    workflow = (ROOT / ".github/workflows/publish-mcp.yml").read_text()
    assert "releases/latest" not in workflow
    assert "releases/download/v${version}" in workflow
    match = re.search(r'expected_sha256="([0-9a-f]{64})"', workflow)
    assert match is not None
    assert match.group(1) != "0" * 64


def test_release_verifies_pypi_serves_the_built_artifacts() -> None:
    # `skip-existing: true` keeps a re-tagged release green even when PyPI already has
    # that version, so a forgotten version bump looks exactly like a real release. The
    # publish job must therefore prove PyPI serves THESE bytes, not just exit 0.
    workflow = (ROOT / ".github/workflows/release.yml").read_text()
    assert "skip-existing: true" in workflow  # deliberate idempotency, still guarded
    assert "Verify PyPI actually serves THESE artifacts" in workflow
    assert "pypi.org/pypi/volante/${version}/json" in workflow
    assert 'digests"]["sha256' in workflow
