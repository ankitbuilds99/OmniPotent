"""The MCP filesystem servers must not be steerable outside TARGET_REPO_PATH.

Each of these tools took a caller-supplied string that reached the filesystem
without a containment check: repo.grep's `glob` (rglob implicitly prefixes
"**/", so "../.env*" climbed out of the repo), docker.analyze_for_docker's
`path` (plain `REPO_PATH / path`), and security.scan_kubernetes'
`manifest_pattern`. scan_kubernetes additionally echoed matched password lines
verbatim. These tests pin the sandbox and the redaction.
"""

import json

import pytest

from app.mcp.servers.docker import server as docker_server
from app.mcp.servers.repo import server as repo_server
from app.mcp.servers.security import server as security_server


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """A repo root with a secret-bearing sibling directory just outside it."""
    root = tmp_path / "repo"
    (root / "k8s").mkdir(parents=True)
    (root / "k8s" / "pod.yaml").write_text(
        'kind: Pod\nprivileged: true\npassword: "InsideSecret999"\n'
    )
    (root / "app.py").write_text("API_KEY = 'inside-only'\n")

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-outside-secret\n")
    (outside / "creds.yaml").write_text('kind: Secret\npassword: OutsideSecret123\n')
    (outside / "package.json").write_text(
        json.dumps({"main": "srv.js", "dependencies": {"express": "^4.0.0"}})
    )

    for module in (repo_server, docker_server, security_server):
        monkeypatch.setattr(module, "REPO_PATH", root)
    return root, outside


def test_grep_rejects_parent_traversal_glob(sandbox):
    with pytest.raises(ValueError, match="escapes the repository sandbox"):
        repo_server.grep("API_KEY", glob="../.env*")


def test_grep_rejects_absolute_glob(sandbox):
    _, outside = sandbox
    with pytest.raises(ValueError, match="must be relative"):
        repo_server.grep("API_KEY", glob=f"{outside}/.env")


def test_grep_still_searches_inside_the_repo(sandbox):
    assert repo_server.grep("API_KEY", glob="*.py") == ["app.py:1: API_KEY = 'inside-only'"]


def test_grep_skips_files_reached_through_a_symlinked_directory(sandbox):
    root, outside = sandbox
    (root / "link").symlink_to(outside, target_is_directory=True)
    # The pattern is legal and the matched file is not itself a symlink, so only
    # the per-match realpath check keeps the outside .env out of the results.
    assert repo_server.grep("API_KEY", glob="link/*") == ["(no matches)"]


def test_analyze_for_docker_rejects_parent_traversal(sandbox):
    with pytest.raises(ValueError, match="escapes the repository sandbox"):
        docker_server.analyze_for_docker("../outside")


def test_analyze_for_docker_rejects_absolute_path(sandbox):
    _, outside = sandbox
    with pytest.raises(ValueError, match="escapes the repository sandbox"):
        docker_server.analyze_for_docker(str(outside))


def test_analyze_for_docker_still_analyses_the_repo(sandbox):
    root, _ = sandbox
    (root / "requirements.txt").write_text("flask\n")
    result = docker_server.analyze_for_docker(".")
    assert result["language"] == "python"
    assert "requirements.txt" in result["detected_files"]


def test_scan_kubernetes_rejects_parent_traversal_pattern(sandbox):
    with pytest.raises(ValueError, match="escapes the repository sandbox"):
        security_server.scan_kubernetes(".", "../*.yaml")


def test_scan_kubernetes_rejects_absolute_pattern(sandbox):
    # Previously this crashed the tool with an uncaught NotImplementedError from
    # pathlib instead of reporting a refusal.
    _, outside = sandbox
    with pytest.raises(ValueError, match="must be relative"):
        security_server.scan_kubernetes(".", f"{outside}/*.yaml")


def test_scan_kubernetes_skips_manifests_symlinked_out_of_the_repo(sandbox):
    root, outside = sandbox
    (root / "k8s" / "creds.yaml").symlink_to(outside / "creds.yaml")
    result = security_server.scan_kubernetes(".", "*.yaml")
    assert result["scanned_files"] == ["k8s/pod.yaml"]
    assert "OutsideSecret123" not in json.dumps(result)


def test_scan_kubernetes_redacts_the_password_it_reports(sandbox):
    result = security_server.scan_kubernetes(".", "*.yaml")
    assert result["scanned_files"] == ["k8s/pod.yaml"]
    password_findings = [f for f in result["findings"] if f["issue"] == "Hardcoded password"]
    assert len(password_findings) == 1
    content = password_findings[0]["content"]
    assert "InsideSecret999" not in content
    assert content.startswith('password: "Insi') and content.endswith('t999"')
    # The finding still locates the problem precisely.
    assert password_findings[0]["line"] == 3
    assert any(f["issue"] == "Privileged container" for f in result["findings"])
