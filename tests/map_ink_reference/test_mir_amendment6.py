"""Amendment 6: preregistration historical identity and execution chain.

Synthetic repositories only; the real repository is read only for the
preregistration file at the pinned chain commits."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import map_ink_reference.seal as seal
import pytest
from map_ink_reference.constants import (
    AMENDMENT4_COMMIT,
    PREREGISTRATION_CHAIN,
    PREREGISTRATION_PATH,
)
from map_ink_reference.errors import PinMismatch, ProcedureOrderError, SealViolation
from mir_helpers import commit, init_repo

REPO = Path(__file__).resolve().parents[2]
AMENDMENT4_SHA256 = "4ffeeaf899185d84beea1e97c59b0baa46798cb7390cf294fae3343804e757e1"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_at(repo: Path, commit_id: str, rel: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repo), "show", f"{commit_id}:{rel}"],
        capture_output=True,
        check=True,
    ).stdout


def _seal_versions(
    repo: Path, prereg: Path, texts: list[str]
) -> list[tuple[str, str, str]]:
    entries = []
    for i, text in enumerate(texts):
        prereg.write_text(text)
        entries.append(
            (
                f"amendment{i + 4}",
                commit(repo, [prereg], f"v{i}"),
                _sha(prereg.read_bytes()),
            )
        )
    return entries


@pytest.fixture
def chain_repo(tmp_path: Path, monkeypatch):
    repo = init_repo(tmp_path / "repo")
    prereg = repo / "prereg.md"
    texts = ["# A4\n", "# A4\n# A5\n", "# A4\n# A5\n# A6\n"]
    entries = _seal_versions(repo, prereg, texts)
    monkeypatch.setattr(seal, "PREREGISTRATION_PATH", "prereg.md")
    monkeypatch.setattr(seal, "PREREGISTRATION_CHAIN", tuple(entries))
    return repo, prereg, entries


def test_real_chain_preserves_the_amendment4_identity() -> None:
    """Read-only: the preregistration file at each pinned real chain commit."""
    assert [entry[0] for entry in PREREGISTRATION_CHAIN] == [
        "amendment4",
        "amendment5",
        "amendment6",
        "amendment7",
        "amendment8",
    ]
    assert PREREGISTRATION_CHAIN[0][1:] == (AMENDMENT4_COMMIT, AMENDMENT4_SHA256)
    previous = b""
    for _, commit_id, digest in PREREGISTRATION_CHAIN:
        content = _file_at(REPO, commit_id, PREREGISTRATION_PATH)
        assert _sha(content) == digest
        assert content.startswith(previous)
        previous = content


def test_chain_verifies_when_head_descends_from_the_last_amendment(chain_repo) -> None:
    repo, _, _ = chain_repo
    seal.verify_preregistration_chain(repo)
    tool = repo / "tool.py"
    tool.write_text("repair = 1\n")
    commit(repo, [tool], "authorized tool repair")
    seal.verify_preregistration_chain(repo)


def test_uncommitted_append_or_edit_stops(chain_repo) -> None:
    repo, prereg, _ = chain_repo
    original = prereg.read_text()
    prereg.write_text(original + "# A7\n")
    with pytest.raises(SealViolation):
        seal.verify_preregistration_chain(repo)
    prereg.write_text(original.replace("A5", "A5 edited"))
    with pytest.raises(SealViolation):
        seal.verify_preregistration_chain(repo)


def test_committed_unauthorized_amendment_stops(chain_repo) -> None:
    repo, prereg, _ = chain_repo
    prereg.write_text(prereg.read_text() + "# A7\n")
    commit(repo, [prereg], "unauthorized amendment 7")
    with pytest.raises(PinMismatch):
        seal.verify_preregistration_chain(repo)


def test_alteration_of_earlier_sealed_text_stops(tmp_path: Path, monkeypatch) -> None:
    repo = init_repo(tmp_path / "repo")
    prereg = repo / "prereg.md"
    entries = _seal_versions(repo, prereg, ["# A4\n", "# A4 rewritten\n# A5\n"])
    monkeypatch.setattr(seal, "PREREGISTRATION_PATH", "prereg.md")
    monkeypatch.setattr(seal, "PREREGISTRATION_CHAIN", tuple(entries))
    with pytest.raises(SealViolation):
        seal.verify_preregistration_chain(repo)


def test_wrong_digest_stops(chain_repo, monkeypatch) -> None:
    repo, _, entries = chain_repo
    bad = list(entries)
    bad[1] = (bad[1][0], bad[1][1], "0" * 64)
    monkeypatch.setattr(seal, "PREREGISTRATION_CHAIN", tuple(bad))
    with pytest.raises(PinMismatch):
        seal.verify_preregistration_chain(repo)


def test_wrong_order_missing_commit_or_empty_chain_stops(chain_repo, monkeypatch) -> None:
    repo, _, entries = chain_repo
    cases = [
        ((entries[1], entries[0], entries[2]), ProcedureOrderError),
        ((entries[0], entries[0], entries[1], entries[2]), ProcedureOrderError),
        (
            (entries[0], ("amendment5", "f" * 40, entries[1][2]), entries[2]),
            SealViolation,
        ),
        ((), SealViolation),
    ]
    for chain, error in cases:
        monkeypatch.setattr(seal, "PREREGISTRATION_CHAIN", chain)
        with pytest.raises(error):
            seal.verify_preregistration_chain(repo)
