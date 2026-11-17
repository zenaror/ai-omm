"""Build a scoped Git credential-store entry without changing the remote URL."""
from urllib.parse import quote, urlsplit


def credential_entry(remote_url: str, username: str, token: str) -> str:
    """Preserve HTTP/HTTPS, explicit ports and IPv6; keep secrets out of argv."""
    try:
        url = urlsplit(remote_url)
        if url.scheme not in {'http', 'https'} or not url.hostname:
            raise ValueError('unsupported credential URL')
        host = url.hostname
        authority = f'[{host}]' if ':' in host else host
        if url.port is not None:
            authority += f':{url.port}'
    except ValueError as exc:
        raise ValueError('Informe um endereço HTTP ou HTTPS válido para o repositório.') from exc
    return f'{url.scheme}://{quote(username, safe="")}:{quote(token, safe="")}@{authority}\n'
