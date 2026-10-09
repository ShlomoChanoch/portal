"""Runner key custody (AD-9) — the runner alone holds the decryption key."""
from __future__ import annotations

from adapters.crypt import load_or_create_keypair
from api.config import Settings


def load_runner_keypair(settings: Settings) -> tuple[bytes, bytes]:
    return load_or_create_keypair(settings.runner_pub_path)