import stat

import pytest

from ebmcp import client

REVOLUT = "sid-revolut-aaaa"
SEB = "sid-seb-bbbb"
MANUAL = "sid-manual-cccc"


@pytest.fixture(autouse=True)
def isolated_sessions(monkeypatch, tmp_path):
    monkeypatch.delenv("ENABLEBANKING_SESSION_ID", raising=False)
    monkeypatch.setattr(client, "SESSION_FILE", str(tmp_path / ".enablebanking_session"))


def test_consent_finish_file_is_visible_to_the_server():
    saved = client.save_session(REVOLUT, "Revolut")
    assert saved.name.endswith(".revolut")
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    assert client.session_ids() == [REVOLUT]


def test_bank_name_becomes_an_alnum_filename():
    saved = client.save_session(REVOLUT, "Avanza Bank")
    assert saved.name.endswith(".avanzabank")
    assert client.session_ids() == [REVOLUT]


def test_second_bank_does_not_replace_the_first():
    client.save_session(REVOLUT, "Revolut")
    client.save_session(SEB, "SEB")
    assert client.session_ids() == [REVOLUT, SEB]


def test_reconsent_replaces_only_that_bank():
    client.save_session(REVOLUT, "Revolut")
    client.save_session(SEB, "SEB")
    client.save_session("sid-revolut-new", "Revolut")
    assert client.session_ids() == ["sid-revolut-new", SEB]


def test_env_overrides_files(monkeypatch):
    client.save_session(REVOLUT, "Revolut")
    monkeypatch.setenv("ENABLEBANKING_SESSION_ID", f"{MANUAL},{SEB}")
    assert client.session_ids() == [MANUAL, SEB]


def test_unsuffixed_session_file_is_still_read(tmp_path):
    (tmp_path / ".enablebanking_session").write_text(f"{MANUAL}\n{SEB}\n")
    assert client.session_ids() == [MANUAL, SEB]


def test_unsuffixed_file_and_per_bank_files_are_merged(tmp_path):
    (tmp_path / ".enablebanking_session").write_text(MANUAL)
    client.save_session(REVOLUT, "Revolut")
    assert client.session_ids() == [MANUAL, REVOLUT]


def test_duplicate_ids_are_kept_once(tmp_path):
    (tmp_path / ".enablebanking_session").write_text(REVOLUT)
    client.save_session(REVOLUT, "Revolut")
    assert client.session_ids() == [REVOLUT]


def test_missing_session_raises():
    with pytest.raises(RuntimeError, match="No session"):
        client.session_ids()


def test_empty_env_falls_through_to_files(monkeypatch):
    client.save_session(REVOLUT, "Revolut")
    monkeypatch.setenv("ENABLEBANKING_SESSION_ID", "")
    assert client.session_ids() == [REVOLUT]


def test_whitespace_env_falls_through_to_files(monkeypatch):
    client.save_session(REVOLUT, "Revolut")
    monkeypatch.setenv("ENABLEBANKING_SESSION_ID", "  ,  ")
    assert client.session_ids() == [REVOLUT]


def test_same_name_different_country_does_not_clobber():
    fi = client.save_session("sid-fi", "Nordea", "FI")
    se = client.save_session("sid-se", "Nordea", "SE")
    assert fi.name.endswith(".nordeafi")
    assert se.name.endswith(".nordease")
    assert client.session_ids() == ["sid-fi", "sid-se"]


def test_directory_with_session_prefix_is_ignored(tmp_path):
    (tmp_path / ".enablebanking_session.revolut").mkdir()
    client.save_session(SEB, "SEB")
    assert client.session_ids() == [SEB]
