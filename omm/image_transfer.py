"""Bounded binary transfer; neither clients nor models need to transcribe base64."""
from __future__ import annotations

from email import policy
from email.parser import BytesParser
import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import uuid
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from .image_evidence import MAX_CARD_BYTES, MAX_IMAGE_BYTES, metadata_values, scope_name

MAX_UPLOAD_BYTES = MAX_IMAGE_BYTES + MAX_CARD_BYTES + 65536


def decode_mcp_image(data_base64: str, image_sha256: str) -> bytes:
    """Decode bounded transport data; never treat a caller path as server-local."""
    if (not isinstance(data_base64, str) or not data_base64
            or len(data_base64) > 4 * ((MAX_IMAGE_BYTES + 2) // 3)):
        raise ValueError("Envie uma imagem de até 10 MiB em base64, gerado pelo cliente.")
    if (not isinstance(image_sha256, str) or len(image_sha256) != 64
            or any(c not in "0123456789abcdef" for c in image_sha256)):
        raise ValueError("Informe o SHA-256 hexadecimal minúsculo do arquivo original.")
    try:
        raw = base64.b64decode(data_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Base64 inválido; envie os bytes codificados pelo cliente, sem prefixo data:.") from exc
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("Cada imagem precisa ter até 10 MiB e não pode estar vazia.")
    if hashlib.sha256(raw).hexdigest() != image_sha256:
        raise ValueError("SHA-256 da imagem não confere; nenhuma evidência foi gravada.")
    return raw


def parse_upload(content_type: str, body: bytes) -> tuple[str, bytes, dict]:
    if len(body) > MAX_UPLOAD_BYTES or len(content_type) > 300 or "\n" in content_type or "\r" in content_type:
        raise ValueError("Envio de imagem grande demais ou inválido.")
    message = BytesParser(policy=policy.default).parsebytes(
        b"Content-Type: " + content_type.encode("ascii") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body)
    if message.get_content_type() != "multipart/form-data" or not message.is_multipart() or message.defects:
        raise ValueError("Envie multipart/form-data com scope, metadata e file.")
    fields = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if part.is_multipart() or part.defects or name not in {"scope", "metadata", "file"} or name in fields:
            raise ValueError("Use exatamente uma ocorrência de scope, metadata e file.")
        if part.get("Content-Transfer-Encoding", "binary").lower() not in {"binary", "8bit", "7bit"}:
            raise ValueError("Envie os bytes do arquivo, sem base64.")
        fields[name] = part.get_payload(decode=True)
    if set(fields) != {"scope", "metadata", "file"}:
        raise ValueError("Faltam scope, metadata ou file no envio.")
    if len(fields["scope"]) > 120 or len(fields["metadata"]) > MAX_CARD_BYTES or len(fields["file"]) > MAX_IMAGE_BYTES:
        raise ValueError("Um dos campos enviados excede o limite.")
    scope = scope_name(fields["scope"].decode("utf-8"))
    metadata = metadata_values(json.loads(fields["metadata"].decode("utf-8")))
    return scope, fields["file"], metadata


def multipart_upload(scope: str, raw: bytes, metadata: dict) -> tuple[str, bytes]:
    scope_name(scope)
    meta = json.dumps(metadata_values(metadata), ensure_ascii=False).encode()
    if len(raw) > MAX_IMAGE_BYTES or len(meta) > MAX_CARD_BYTES:
        raise ValueError("Imagem ou descrição grande demais.")
    boundary = "omm-" + uuid.uuid4().hex
    parts = []
    for name, value, extra in (("scope", scope.encode(), ""), ("metadata", meta, ""),
                               ("file", raw, '; filename="evidence.bin"')):
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"{extra}\r\n\r\n'.encode() + value + b"\r\n")
    return "multipart/form-data; boundary=" + boundary, b"".join(parts) + f"--{boundary}--\r\n".encode()


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward credentials or evidence to another endpoint.


def upload_to_server(server_url: str, scope: str, raw: bytes, metadata: dict,
                     token_env: str = "OMM_MCP_TOKEN") -> dict:
    url = urlsplit(server_url)
    if url.scheme not in {"http", "https"} or not url.netloc or url.username or url.password or url.query or url.fragment:
        raise ValueError("Informe a URL do painel sem usuário, senha, query ou fragmento.")
    token = os.getenv(token_env, "")
    if not token or any(c in token for c in "\r\n"):
        raise ValueError("Defina o token MCP na variável de ambiente indicada, sem gravá-lo no manifesto.")
    content_type, body = multipart_upload(scope, raw, metadata)
    request = Request(server_url.rstrip("/") + "/api/images", data=body,
                      headers={"Content-Type": content_type, "Authorization": "Bearer " + token}, method="POST")
    with build_opener(_NoRedirect).open(request, timeout=60) as response:
        result = json.loads(response.read(65536))
    return result


def read_local_image(path: Path) -> bytes:
    with path.open("rb") as stream:
        raw = stream.read(MAX_IMAGE_BYTES + 1)
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("Cada imagem precisa ter até 10 MiB e não pode estar vazia.")
    return raw


def upload_to_mcp(server_url: str, scope: str, raw: bytes, metadata: dict,
                  token_env: str = "OMM_MCP_TOKEN") -> dict:
    """Transfer a local file through the MCP endpoint without model transcription."""
    import asyncio
    url = urlsplit(server_url)
    if url.scheme not in {"http", "https"} or not url.netloc or url.username or url.password or url.query or url.fragment:
        raise ValueError("Informe a URL MCP sem credenciais, query ou fragmento.")
    token = os.getenv(token_env, "")
    if not token or any(c in token for c in "\r\n"):
        raise ValueError("Defina o token MCP na variável de ambiente indicada.")
    scope_name(scope)
    metadata_values(metadata)
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("Cada imagem precisa ter até 10 MiB e não pode estar vazia.")
    try:
        import httpx2
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
    except ImportError as exc:
        raise ValueError("Para envio MCP instale a OMM com o extra mcp: pip install 'ai-omm[mcp]'.") from exc

    async def send():
        async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + token},
                                     timeout=60, follow_redirects=False) as http:
            async with streamable_http_client(server_url, http_client=http) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    result = await session.call_tool("upload_image_evidence", {
                        "scope": scope, "data_base64": base64.b64encode(raw).decode("ascii"),
                        "image_sha256": hashlib.sha256(raw).hexdigest(), "metadata": metadata})
                    if result.is_error:
                        raise ValueError("O servidor recusou o envio MCP. Confira formato, metadados, cota e versão da OMM.")
                    return json.loads(next(item.text for item in result.content if item.type == "text"))
    async def bounded():
        return await asyncio.wait_for(send(), timeout=90)
    try:
        return asyncio.run(bounded())
    except Exception as exc:
        # Transport exceptions can contain request bodies or credentials; never print them.
        raise ValueError("Envio MCP não confirmado. Confira endereço, acesso, extra mcp e versão do servidor; consulte a listagem antes de repetir.") from None
