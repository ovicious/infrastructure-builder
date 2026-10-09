from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from infra_builder.config_io import ConfigError, load_config, save_config
from infra_builder.models import InfraConfig
from infra_builder.validator import validate_config


def test_config_defaults_and_yaml_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "infra.yaml"

    save_config(InfraConfig(), path)
    loaded = load_config(path)

    assert loaded == InfraConfig()
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["schema_version"] == 1


@pytest.mark.parametrize("schema_version", [0, 2, "1", True])
def test_unsupported_schema_version_is_rejected(schema_version: object) -> None:
    with pytest.raises(ValidationError, match="schema_version"):
        InfraConfig.model_validate({"schema_version": schema_version})


def test_unknown_configuration_fields_are_rejected() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        InfraConfig.model_validate({"password": "not-allowed"})


@pytest.mark.parametrize(
    "data",
    [
        {"application": {"port": 0}},
        {"application": {"port": 65536}},
        {"application": {"port": True}},
        {"project": {"name": "../unsafe"}},
        {"services": {"postgres": {"password_env": "not a valid environment name"}}},
    ],
)
def test_invalid_configuration_values_are_rejected(data: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        InfraConfig.model_validate(data)


def test_yaml_must_contain_a_mapping(tmp_path: Path) -> None:
    path = tmp_path / "infra.yaml"
    path.write_text("- not\n- a mapping\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="YAML mapping"):
        load_config(path)


def test_output_directory_cannot_escape_config_directory(tmp_path: Path) -> None:
    config = InfraConfig(project={"output_dir": "./generated"})
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    (tmp_path / "generated").symlink_to(outside)

    with pytest.raises(ConfigError, match="outside the config directory"):
        validate_config(config, tmp_path)


def test_unsupported_centralized_logs_are_rejected(tmp_path: Path) -> None:
    config = InfraConfig(observability={"centralized_logs": True})

    with pytest.raises(ConfigError, match="not supported yet"):
        validate_config(config, tmp_path)


def test_output_directory_symlink_loop_is_reported(tmp_path: Path) -> None:
    config = InfraConfig(project={"output_dir": "./generated"})
    (tmp_path / "generated").symlink_to("generated")

    with pytest.raises(ConfigError, match="cannot safely resolve"):
        validate_config(config, tmp_path)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (
            {
                "application": {"name": "postgres"},
                "services": {"postgres": {"enabled": True}},
            },
            "conflicts",
        ),
        (
            {
                "application": {"name": "redis"},
                "services": {"redis": {"enabled": True}},
            },
            "conflicts",
        ),
        (
            {
                "application": {"name": "prometheus"},
                "observability": {"metrics": True},
            },
            "conflicts",
        ),
        (
            {
                "application": {"port": 9090},
                "observability": {"metrics": True},
            },
            "conflicts",
        ),
        (
            {
                "runtime": {"type": "host_systemd"},
                "services": {"postgres": {"enabled": True}},
            },
            "supported only",
        ),
    ],
)
def test_incompatible_service_configuration_is_rejected(
    data: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        InfraConfig.model_validate(data)
