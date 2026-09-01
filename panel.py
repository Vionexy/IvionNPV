import json
import uuid as uuid_lib
from urllib.parse import quote, urlsplit

import httpx

from config import (
    PANEL_URL,
    PANEL_USERNAME,
    PANEL_PASSWORD,
    PANEL_WS_INBOUND_ID,
    MEDIA_DOMAIN,
    MEDIA_PORT,
)

_parts = urlsplit(PANEL_URL)
_HEADERS = {
    "Origin": f"{_parts.scheme}://{_parts.netloc}",
    "Referer": f"{PANEL_URL}/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
}


async def _login(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        f"{PANEL_URL}/login",
        json={"username": PANEL_USERNAME, "password": PANEL_PASSWORD},
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"Не удалось войти в панель: {data.get('msg')}")


async def _get_inbound(client: httpx.AsyncClient, inbound_id: int) -> dict:
    resp = await client.get(f"{PANEL_URL}/panel/api/inbounds/get/{inbound_id}")
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"Не удалось получить инбаунд: {data.get('msg')}")
    return data["obj"]


async def _add_client(
    client: httpx.AsyncClient,
    inbound_id: int,
    client_uuid: str,
    email: str,
) -> None:
    settings = {
        "clients": [
            {
                "id": client_uuid,
                "email": email,
                "enable": True,
                "flow": "",
                "limitIp": 1,
                "totalGB": 0,
                "expiryTime": 0,
            }
        ]
    }
    resp = await client.post(
        f"{PANEL_URL}/panel/api/inbounds/addClient",
        json={"id": inbound_id, "settings": json.dumps(settings)},
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"Не удалось добавить клиента: {data.get('msg')}")


def _build_vless_link(inbound: dict, client_uuid: str, email: str) -> str:
    # TLS для этого сетапа обеспечивает Cloudflare Edge, а не сам Xray —
    # в streamSettings инбаунда стоит security:none (локально между
    # туннелем и Xray трафик уже расшифрован). Поэтому в ссылке для
    # клиента TLS-параметры задаём явно, из MEDIA_DOMAIN, а не берём
    # из инбаунда. Путь берём из инбаунда динамически — он может меняться.
    stream = json.loads(inbound["streamSettings"])
    ws = stream.get("wsSettings", {})
    path = ws.get("path", "/")

    params = {
        "encryption": "none",
        "type": "ws",
        "security": "tls",
        "path": path,
        "host": MEDIA_DOMAIN,
        "sni": MEDIA_DOMAIN,
        "alpn": "http/1.1",
    }

    query = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params.items())
    return f"vless://{client_uuid}@{MEDIA_DOMAIN}:{MEDIA_PORT}?{query}#{quote(email)}"


async def create_client_link(email: str) -> str:
    client_uuid = str(uuid_lib.uuid4())
    async with httpx.AsyncClient(timeout=15, headers=_HEADERS) as client:
        await _login(client)
        await _add_client(client, PANEL_WS_INBOUND_ID, client_uuid, email)
        inbound = await _get_inbound(client, PANEL_WS_INBOUND_ID)
    return _build_vless_link(inbound, client_uuid, email)
