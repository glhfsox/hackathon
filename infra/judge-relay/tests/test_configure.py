from pathlib import Path

import pytest
import yaml

from scripts.configure_judge_relay import configure
from scripts.deploy_judge_relay import credentials, upload

ROOT = Path(__file__).resolve().parents[3]


def test_generated_policy_preserves_checks_and_removes_provider_keys(tmp_path):
    (tmp_path / "backend").mkdir()
    original = yaml.safe_load((ROOT / "backend/policy.yaml").read_text())
    (tmp_path / "backend/policy.yaml").write_text(yaml.safe_dump(original))
    policy_file, compose_file = configure(tmp_path, "https://relay.example")
    policy = yaml.safe_load(policy_file.read_text())
    assert policy["models"]["gpt-4o-mini"]["api_key_env"] is None
    assert policy["jev"]["api_key_env"] is None
    assert policy["jev"]["fallback"]["api_key_env"] is None
    assert policy["models"]["gpt-4o-mini"]["upstream_base_url"] == "https://relay.example/openai/v1"
    assert policy["jev"]["base_url"] == "https://relay.example/typesafe"
    assert policy["roles"] == original["roles"]
    for check in original["checks"]:
        if check != "jev":
            assert policy["checks"][check] == original["checks"][check]
    override = yaml.safe_load(compose_file.read_text())
    assert override["services"]["backend"]["environment"]["OPENAI_API_KEY"] == ""


@pytest.mark.parametrize(
    "url", ["http://evil", "https://key@relay.test", "https://relay.test?key=x"]
)
def test_bad_relay_url_rejected(tmp_path, url):
    with pytest.raises(ValueError):
        configure(tmp_path, url)


def test_secret_parser_never_executes_shell(tmp_path, monkeypatch):
    for name in ("OPENAI_API_KEY", "TYPESAFE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / ".env"
    path.write_text("OPENAI_API_KEY='$(do-not-execute)'\nTYPESAFE_API_KEY=typesafe-value\n")
    assert credentials(path)["OPENAI_API_KEY"] == "$(do-not-execute)"


def test_upload_uses_stdin_and_returns_only_version(monkeypatch, tmp_path):
    from types import SimpleNamespace

    def subprocess_run(argv, **kwargs):
        assert "private-key" not in str(argv)
        assert kwargs["input"] == "private-key"
        assert "--data-file=-" in argv
        return SimpleNamespace(returncode=0, stdout="projects/test/secrets/openai/versions/3\n")

    monkeypatch.setattr("scripts.deploy_judge_relay.subprocess.run", subprocess_run)
    assert upload("test", "openai", "private-key", tmp_path) == "3"
