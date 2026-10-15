from datetime import date
from pathlib import Path

import pytest

from scripts import check_compose, check_secrets, new_adr


def _write(tmp: Path, text: str) -> Path:
    p = check_compose.ROOT / "tests" / "stack" / "_tmp_test.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


@pytest.fixture
def compose_file():
    created: list[Path] = []

    def make(text: str) -> Path:
        p = _write(Path(), text)
        created.append(p)
        return p

    yield make
    for p in created:
        p.unlink(missing_ok=True)


def test_compose_good_file_passes(compose_file) -> None:
    p = compose_file(
        "services:\n  api:\n    image: x@sha256:" + "a" * 64 + "\n"
        "    ports: ['127.0.0.1:8000:8000']\n    mem_limit: 256m\n"
    )
    assert check_compose.check_file(p) == []


def test_compose_violations_are_all_reported(compose_file) -> None:
    p = compose_file(
        "services:\n  api:\n    image: python:3.12\n    ports: ['8000:8000']\n"
        "    privileged: true\n    volumes: ['/var/run/docker.sock:/var/run/docker.sock']\n"
    )
    problems = " ".join(check_compose.check_file(p))
    for needle in ("not pinned by digest", "127.0.0.1", "privileged", "Docker socket", "memory"):
        assert needle in problems


def test_secret_patterns_detect_and_allow(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(check_secrets, "ROOT", tmp_path)
    leaked = tmp_path / "a.txt"
    leaked.write_text("token = ghp_" + "A" * 36 + "\n")
    allowed = tmp_path / "b.txt"
    allowed.write_text("ghp_" + "B" * 36 + "  # secret-scan: allow\n")
    fake_enc = tmp_path / "dev.enc.env"
    fake_enc.write_text("PASSWORD=plain\n")
    assert check_secrets.scan(leaked)
    assert check_secrets.scan(allowed) == []
    assert "not SOPS-encrypted" in check_secrets.scan(fake_enc)[0]


def test_new_adr_numbers_and_fills_template(tmp_path: Path) -> None:
    (tmp_path / "template.md").write_text("# ADR NNNN: <Title>\n\nDate: YYYY-MM-DD\n")
    (tmp_path / "0007-old.md").write_text("x")
    path = new_adr.create("Use Valkey for queues!", tmp_path, date(2026, 10, 15))
    assert path.name == "0008-use-valkey-for-queues.md"
    assert path.read_text() == "# ADR 0008: Use Valkey for queues!\n\nDate: 2026-10-15\n"
