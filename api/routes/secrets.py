"""Runtime secrets (FR-13, AD-9).

Values are masked on input, stored envelope-encrypted (api encrypts with the
runner's public key; the api never holds a decryption key), and injected at run
time only. They are never returned to any surface — names only.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.bootstrap import Workspace, encrypt_secret
from api.deps import get_tenant, get_ws
from api.routes.deployments import _own
from api.schemas import SecretRequest
from core.errors import NotFound, PortalError

router = APIRouter(prefix="/api/deployments/{slug}/secrets", tags=["secrets"])


@router.get("")
def list_secrets(slug: str, tenant: dict = Depends(get_tenant), ws: Workspace = Depends(get_ws)):
    _own(ws, tenant, slug)
    return {"secrets": ws.store.list_secrets(tenant["id"], slug)}


@router.put("")
def upsert_secret(slug: str, body: SecretRequest, tenant: dict = Depends(get_tenant),
                  ws: Workspace = Depends(get_ws)):
    _own(ws, tenant, slug)
    env = encrypt_secret(ws, body.value)
    ws.store.upsert_secret(tenant["id"], slug, body.key, env["ciphertext"],
                           env["alg"], env["sender_pk"], env["nonce"])
    return {"secret": {"key": body.key, "masked": True}}


@router.delete("/{key}")
def delete_secret(slug: str, key: str, tenant: dict = Depends(get_tenant),
                  ws: Workspace = Depends(get_ws)):
    _own(ws, tenant, slug)
    if not ws.store.delete_secret(tenant["id"], slug, key):
        raise NotFound("No such secret.")
    return {"ok": True}