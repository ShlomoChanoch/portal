"""Envelope crypto round-trip + failure modes (AD-9)."""
from __future__ import annotations

import pytest

from adapters.crypt import (
    decrypt_from_api,
    encrypt_for_holder,
    load_existing_keypair,
    load_or_create_keypair,
)


def _pair(tmp_path, name="k"):
    a = load_or_create_keypair(tmp_path / name)
    b = load_or_create_keypair(tmp_path / (name + "2"))
    return a, b


def test_encrypt_decrypt_roundtrip(tmp_path):
    (api_pub, api_sec), (run_pub, run_sec) = _pair(tmp_path)
    env = encrypt_for_holder(api_pub, api_sec, run_pub, "DATABASE_URL=postgres://x")
    assert env["alg"] == "x25519xsalsa20poly1305"
    assert decrypt_from_api(run_sec, env) == "DATABASE_URL=postgres://x"


def test_holder_mismatch_fails_to_decrypt(tmp_path):
    (api_pub, api_sec), (run_pub, _run_sec) = _pair(tmp_path)
    other_pub, other_sec = load_or_create_keypair(tmp_path / "temp-not-recipient")
    env = encrypt_for_holder(api_pub, api_sec, run_pub, "mounted")
    with pytest.raises(ValueError, match="cannot be decrypted"):
        decrypt_from_api(other_sec, env)


def test_tampered_ciphertext_fails(tmp_path):
    (api_pub, api_sec), (run_pub, run_sec) = _pair(tmp_path)
    env = encrypt_for_holder(api_pub, api_sec, run_pub, "sealed")
    env["ciphertext"] = env["ciphertext"][:-8] + "AAAAAAAA"
    with pytest.raises(ValueError, match="cannot be decrypted"):
        decrypt_from_api(run_sec, env)


def test_load_existing_keypair_fails_fast_on_missing(tmp_path):
    with pytest.raises(FileNotFoundError, match="missing keypair"):
        load_existing_keypair(tmp_path / "nope")


def test_keypair_files_are_persisted(tmp_path):
    pub, sec = load_or_create_keypair(tmp_path / "api.key", )
    assert (tmp_path / "api.key").exists()
    assert (tmp_path / "api.key.pub").exists()
    reloaded = load_existing_keypair(tmp_path / "api.key")
    assert reloaded == (pub, sec)