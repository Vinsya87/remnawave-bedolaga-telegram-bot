"""Factory for creating Bot instances with proxy support."""

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.config import settings


def create_bot(token: str | None = None, **kwargs) -> Bot:
    """Create a Bot instance with SOCKS5 proxy session if PROXY_URL is configured."""
    proxy_url = settings.get_proxy_url()
    session = None
    if proxy_url:
        from aiogram.client.session.aiohttp import AiohttpSession
        
        if proxy_url.startswith('socks5'):
            from aiohttp_socks import ProxyConnector
            import aiohttp
            
            class CustomAiohttpSession(AiohttpSession):
                def __init__(self, socks_proxy: str):
                    super().__init__()
                    self.socks_proxy = socks_proxy
                    
                async def create_session(self) -> aiohttp.ClientSession:
                    connector = ProxyConnector.from_url(self.socks_proxy)
                    return aiohttp.ClientSession(connector=connector)
                    
            session = CustomAiohttpSession(socks_proxy=proxy_url)
        else:
            session = AiohttpSession(proxy=proxy_url)

    kwargs.setdefault('default', DefaultBotProperties(parse_mode=ParseMode.HTML))
    return Bot(token=token or settings.BOT_TOKEN, session=session, **kwargs)
