import pytest

from app.bot_factory import SocksAiohttpSession, _normalize_socks_proxy_url


def test_normalize_socks5_proxy_url_keeps_local_dns() -> None:
    assert _normalize_socks_proxy_url('socks5://user:pass@proxy.local:1080') == (
        'socks5://user:pass@proxy.local:1080',
        None,
    )


def test_normalize_socks5h_proxy_url_enables_remote_dns() -> None:
    assert _normalize_socks_proxy_url('socks5h://user:pass@proxy.local:1080') == (
        'socks5://user:pass@proxy.local:1080',
        True,
    )


@pytest.mark.asyncio
async def test_socks_session_is_reused_and_closed() -> None:
    session = SocksAiohttpSession('socks5h://user:pass@127.0.0.1:1080')

    first = await session.create_session()
    second = await session.create_session()

    assert first is second
    assert session.proxy_url == 'socks5://user:pass@127.0.0.1:1080'
    assert session.rdns is True

    await session.close()

    assert first.closed
