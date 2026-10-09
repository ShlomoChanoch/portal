"""Envelope encryption for secrets (AD-9).

Pinned envelope schema:
    {"alg": "x25519xsalsa20poly1305",
     "sender_pk": <api ephemeral-ish public key (base64)>,
     "nonce":     <base64>,
     "ciphertext":<base64>}

Key custody:
  * The runner alone holds the decrypting keypair. The runner private key is
    never stored beside ciphertext and, in prod, is readable only by the runner
    user (see ops/).
  * The api is the encrypter: it holds its own keypair plus the runner's public
    key, and writes ciphertext only — no key material that can decrypt.
  * Secrets are scoped to a Deployment and injected only at run time; they never
    reach a Build (FR-13, NFR-2c).
"""

from __future__ import annotations

import base64
import json
import os
import secrets
from pathlib import Path

from nacl import bindings, encoding
from nacl.exceptions import CryptoError
from nacl.public import Box, PrivateKey, PublicKey

ALG = "x25519xsalsa20poly1305"


def load_or_create_keypair(path: str | Path) -> tuple[bytes, bytes]:
    """Load an X25519 keypair or generate + persist it. Returns (public, secret)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pub_path = Path(str(path) + ".pub")
    if path.exists() and pub_path.exists():
        priv = base64.b64decode(path.read_text().strip())
        pub = base64.b64decode(pub_path.read_text().strip())
        return pub, priv
    sk = PrivateKey.generate()
    pub = base64.b64encode(bytes(sk.public_key)).decode()
    priv = base64.b64encode(bytes(sk)).decode()
    path.write_text(priv)
    pub_path.write_text(pub)
    try:
        os.chmod(path, 0o600)
        os.chmod(pub_path, 0o644)
    except OSError:
        pass
    return bytes(sk.public_key), bytes(sk)


def load_existing_keypair(path: str | Path) -> tuple[bytes, bytes]:
    """Load a keypair that ops/ already provisioned. Fails fast on absence —
    prod never auto-creates key material."""
    path = Path(path)
    pub_path = Path(str(path) + ".pub")
    if not (path.exists() and pub_path.exists()):
        raise FileNotFoundError(f"missing keypair (provision via ops/): {path}")
    priv = base64.b64decode(path.read_text().strip())
    pub = base64.b64decode(pub_path.read_text().strip())
    return pub, priv


def encrypt_for_holder(api_public: bytes, api_secret: bytes,
                       holder_pub: bytes, plaintext: str) -> dict:
    """Encrypt `plaintext` for the holder's public key (api side).

    sender = api keypair, recipient = runner public key. All keys here are
    raw 32-byte values (AD-9); only the envelope carries base64 fields.
    """
    box = Box(PrivateKey(api_secret), PublicKey(holder_pub))
    nonce = secrets.token_bytes(24)
    ct = box.encrypt(plaintext.encode("utf-8"), nonce).ciphertext
    return {
        "alg": ALG,
        "sender_pk": base64.b64encode(api_public).decode(),
        "nonce": base64.b64encode(nonce).decode(),
        "ciphertext": base64.b64encode(ct).decode(),
    }


def decrypt_from_api(holder_secret: bytes, envelope: dict) -> str:
    """Decrypt an envelope issued by the api (runner side, holder=runner)."""
    try:
        box = Box(
            PrivateKey(holder_secret),
            PublicKey(base64.b64decode(envelope["sender_pk"])),
        )
        ct = base64.b64decode(envelope["ciphertext"])
        nonce = base64.b64decode(envelope["nonce"])
        return box.decrypt(ct, nonce).decode("utf-8")
    except (CryptoError, KeyError, ValueError) as exc:
        raise ValueError("secret cannot be decrypted (key mismatch or corruption)") from exc


def envelope_to_json(envelope: dict) -> str:
    return json.dumps(envelope)


def envelope_from_json(raw: str) -> dict:
    env = json.loads(raw)
    if env.get("alg") != ALG:
        raise ValueError("unknown envelope algorithm")
    return env