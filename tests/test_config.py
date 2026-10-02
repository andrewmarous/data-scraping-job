import os
from pathlib import Path
import pytest
from market_data.config import load_config, load_env_file, verify_config
from market_data.errors import ConfigError


def test_paths_resolve_from_configuration(tmp_path):
    path = tmp_path / "config.toml"; path.write_text('[data]\ndirectory="state"\n')
    assert load_config(path).data_dir == tmp_path / "state"

def test_unknown_key_is_rejected(tmp_path):
    path = tmp_path / "config.toml"; path.write_text('[data]\nunknown=true\n')
    with pytest.raises(ConfigError): load_config(path)

def test_env_requires_private_permissions(tmp_path):
    path = tmp_path / ".env"; path.write_text("OPENROUTER_API_KEY=secret\n"); path.chmod(0o644)
    with pytest.raises(ConfigError): load_env_file(path)
    path.chmod(0o600)
    assert load_env_file(path) == {"OPENROUTER_API_KEY":"secret"}

def test_all_source_configuration_and_disabled_credentials(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text('''
[openrouter]
enabled = false
[vast]
enabled = false
contracts = ["interruptible"]
[aws_spot]
enabled = false
regions = ["us-east-1"]
instance_families = ["g5"]
''')
    config = load_config(path)
    assert config.vast_contracts == ("interruptible",)
    assert config.aws_regions == ("us-east-1",)
    verify_config(config)

def test_unverified_gpu_index_cannot_be_enabled(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[openrouter]\nenabled=false\n[gpu_indexes]\nenabled_sources=["computeprices"]\n')
    with pytest.raises(ConfigError, match="verification gate"):
        load_config(path)


def test_enabled_source_requires_its_credential(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text("[openrouter]\nenabled=false\n[vast]\nenabled=true\n")
    monkeypatch.delenv("VAST_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="VAST_API_KEY"):
        verify_config(load_config(path))


def test_launchd_install_validation_allows_pending_provider_account_data(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('''
[openrouter]
enabled=false
[provider_catalogs]
enabled_sources=["gcore","saladcloud"]
gcore_project_id="REPLACE_WITH_GCORE_PROJECT_ID"
gcore_region_ids=["REPLACE_WITH_GCORE_REGION_ID"]
salad_organization="REPLACE_WITH_SALAD_ORGANIZATION"
''')
    verify_config(load_config(path), require_credentials=False)

def test_env_rejects_expressions_and_symlinks(tmp_path):
    path = tmp_path / ".env"
    path.write_text("OPENROUTER_API_KEY=$(command)\n"); path.chmod(0o600)
    with pytest.raises(ConfigError): load_env_file(path)
    target = tmp_path / "target"; target.write_text("OPENROUTER_API_KEY=x\n"); target.chmod(0o600)
    link = tmp_path / "link"; link.symlink_to(target)
    with pytest.raises(ConfigError): load_env_file(link)
