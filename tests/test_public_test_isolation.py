"""The suite never reads the developer's ``.env`` or touches a non-test database."""

import os
import subprocess
import sys
from pathlib import Path

import public_site.settings as public_settings
import pytest
from conftest import confine_database_url
from public_site.settings import PublicSettings

_TOOLS = Path(__file__).resolve().parents[1] / "tools"
_CHILD_PROBE = """
from urllib.parse import urlsplit
from public_site.settings import PublicSettings
settings = PublicSettings.from_env()
print(urlsplit(settings.database_url).path.lstrip("/"), bool(settings.turnstile_secret))
"""

_SENTINELS = {
    "PUBLIC_TURNSTILE_SECRET": "sentinel-turnstile",
    "PUBLIC_DATABASE_URL": "postgresql://coach@127.0.0.1:5432/coach",
}


def _project_with_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lines = [f"{name}={value}" for name, value in _SENTINELS.items()]
    (tmp_path / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(public_settings, "PROJECT_ROOT", tmp_path)
    for name in _SENTINELS:
        # setenv first so monkeypatch also removes anything load_dotenv adds.
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)


def test_suite_never_loads_the_repo_dotenv(tmp_path: Path, monkeypatch) -> None:
    assert os.environ.get("PUBLIC_LOAD_DOTENV") == "0"
    _project_with_dotenv(tmp_path, monkeypatch)
    settings = PublicSettings.from_env()
    assert not settings.turnstile_secret
    assert not settings.database_url


def test_dotenv_still_loads_outside_the_suite(tmp_path: Path, monkeypatch) -> None:
    _project_with_dotenv(tmp_path, monkeypatch)
    monkeypatch.setenv("PUBLIC_LOAD_DOTENV", "1")
    settings = PublicSettings.from_env()
    assert settings.turnstile_secret == _SENTINELS["PUBLIC_TURNSTILE_SECRET"]
    assert settings.database_url == _SENTINELS["PUBLIC_DATABASE_URL"]


@pytest.mark.parametrize(
    ("conninfo", "kwargs"),
    [
        ("postgresql://coach:secret@127.0.0.1:9/coach", {}),
        ("host=127.0.0.1 port=9 dbname=coach", {}),
        ("postgresql://coach@127.0.0.1:9/coach_test", {"dbname": "coach"}),
        ("postgresql://coach@127.0.0.1:9", {}),
    ],
)
def test_suite_refuses_non_test_databases(conninfo: str, kwargs: dict) -> None:
    psycopg = pytest.importorskip("psycopg")
    with pytest.raises(RuntimeError, match="refused a connection"):
        psycopg.connect(conninfo, connect_timeout=1, **kwargs)


@pytest.mark.parametrize(
    "name", ["coach_test", "coach_test_language_migration", "postgres"]
)
def test_suite_lets_test_databases_through(name: str) -> None:
    psycopg = pytest.importorskip("psycopg")
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(f"postgresql://coach@127.0.0.1:9/{name}", connect_timeout=1)


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (
            "postgresql://coach:pw@127.0.0.1:5432/coach?sslmode=disable",
            "postgresql://coach:pw@127.0.0.1:5432/coach_test?sslmode=disable",
        ),
        ("postgresql://coach@db:5432/coach_test_scratch", "postgresql://coach@db:5432/coach_test_scratch"),
        ("", ""),
    ],
)
def test_inherited_database_url_is_confined_to_test_databases(
    given: str, expected: str
) -> None:
    env = {"PUBLIC_DATABASE_URL": given}
    confine_database_url(env)
    assert env["PUBLIC_DATABASE_URL"] == expected


def test_keyword_database_url_is_confined_to_test_databases() -> None:
    pytest.importorskip("psycopg")
    from psycopg.conninfo import conninfo_to_dict

    env = {"PUBLIC_DATABASE_URL": "host=127.0.0.1 port=5432 dbname=coach user=coach"}
    confine_database_url(env)
    assert conninfo_to_dict(env["PUBLIC_DATABASE_URL"])["dbname"] == "coach_test"


def test_child_processes_inherit_no_live_database_or_dotenv(tmp_path: Path) -> None:
    env = {**os.environ, "PYTHONPATH": str(_TOOLS)}
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD_PROBE],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    database, has_turnstile = proc.stdout.split()
    assert database == "" or database.startswith("coach_test")
    assert has_turnstile == "False"
