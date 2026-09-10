from pathlib import Path

import pytest
import yaml


COMPOSE = Path("deploy/postgres/docker-compose.yml")
ENV_EXAMPLE = Path("deploy/postgres/.env.example")


def _services():
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]


@pytest.mark.parametrize(
    ("service", "key", "variable"),
    [
        ("postgres", "POSTGRES_PASSWORD", "POSTGRES_PASSWORD"),
        ("pgadmin", "PGADMIN_DEFAULT_PASSWORD", "PGADMIN_PASSWORD"),
    ],
)
def test_passwords_must_be_provided_without_defaults(service, key, variable):
    value = _services()[service]["environment"][key]

    # `${VAR:?msg}`：未设置或为空时 compose 拒绝启动（无内置弱口令回退）
    assert value.startswith(f"${{{variable}:?")
    assert ":-" not in value
    assert "stability_password" not in value


@pytest.mark.parametrize("service", ["postgres", "pgadmin"])
def test_published_ports_default_to_loopback_only(service):
    ports = _services()[service]["ports"]

    assert ports
    for entry in ports:
        assert entry.startswith("127.0.0.1:")


def test_env_example_leaves_required_passwords_empty():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    password_lines = [
        line
        for line in text.splitlines()
        if line.startswith(("POSTGRES_PASSWORD=", "PGADMIN_PASSWORD="))
    ]

    assert len(password_lines) == 2
    assert all(line.endswith("=") for line in password_lines)
