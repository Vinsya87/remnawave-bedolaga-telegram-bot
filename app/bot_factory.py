"""Factory for creating Bot instances with proxy and custom API server support."""

from urllib.parse import urlsplit, urlunsplit

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession, SERVER_SOFTWARE, USER_AGENT, __version__
from aiogram.enums import ParseMode

from app.config import settings


def _normalize_socks_proxy_url(proxy_url: str) -> tuple[str, bool | None]:
    """Return a python-socks compatible URL and remote-DNS flag."""
    parsed = urlsplit(proxy_url)
    if parsed.scheme != 'socks5h':
        return proxy_url, None

    return urlunsplit(('socks5', parsed.netloc, parsed.path, parsed.query, parsed.fragment)), True


class SocksAiohttpSession(AiohttpSession):
    """Aiogram aiohttp session backed by aiohttp-socks.

    aiogram's regular AiohttpSession caches and closes its ClientSession.
    Keep the same lifecycle here; otherwise every Telegram request creates
    a session that asyncio later reports as unclosed.
    """

    def __init__(self, proxy_url: str, **kwargs):
        super().__init__(**kwargs)
        self.proxy_url, self.rdns = _normalize_socks_proxy_url(proxy_url)

    async def close(self):
        if self._session and not self._session.closed:
            # Явно закрываем connector чтобы избежать asyncio-предупреждений
            # «Unclosed connector» от aiohttp_socks.ProxyConnector при пересоздании сессии.
            connector = getattr(self._session, 'connector', None)
            await self._session.close()
            if connector and not connector.closed:
                await connector.close()
        self._session = None
        await super().close()

    async def create_session(self):
        import aiohttp
        from aiohttp_socks import ProxyConnector

        if self._should_reset_connector:
            await self.close()

        if self._session is None or self._session.closed:
            connector_kwargs = {}
            if self.rdns is not None:
                connector_kwargs['rdns'] = self.rdns

            self._session = aiohttp.ClientSession(
                connector=ProxyConnector.from_url(self.proxy_url, **connector_kwargs),
                headers={
                    USER_AGENT: f'{SERVER_SOFTWARE} aiogram/{__version__}',
                },
            )
            self._should_reset_connector = False

        return self._session


def create_bot(token: str | None = None, **kwargs) -> Bot:
    """Create a Bot instance with SOCKS5 proxy and/or custom Telegram API server."""
    proxy_url = settings.get_proxy_url()
    telegram_api_url = settings.get_telegram_api_url()
    session = None
    if proxy_url or telegram_api_url:
        from aiogram.client.session.aiohttp import AiohttpSession
        from aiogram.client.telegram import TelegramAPIServer

        session_kwargs: dict = {}
        if telegram_api_url:
            session_kwargs['api'] = TelegramAPIServer.from_base(telegram_api_url)

        if proxy_url:
            if proxy_url.startswith('socks5'):
                session = SocksAiohttpSession(proxy_url=proxy_url, **session_kwargs)
            else:
                session_kwargs['proxy'] = proxy_url
                session = AiohttpSession(**session_kwargs)
        else:
            session = AiohttpSession(**session_kwargs)

    kwargs.setdefault('default', DefaultBotProperties(parse_mode=ParseMode.HTML))
    return Bot(token=token or settings.BOT_TOKEN, session=session, **kwargs)
