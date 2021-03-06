import argparse
from asyncio import get_event_loop
from asyncio import sleep as asleep
from asyncio import TimeoutError
from errno import ECONNRESET
from functools import lru_cache
from functools import partial
from urllib.parse import urljoin

from aioconsole.stream import aprint
from aiohttp import ClientOSError
from aiohttp import ClientPayloadError
from aiohttp import ClientSession
from aiohttp import ClientTimeout
from aiohttp import InvalidURL
from aiohttp import TCPConnector
from lxml.etree import ParserError
from lxml.etree import XMLSyntaxError
from lxml.html import fromstring
from lxml.html import HTMLParser

from utils.cache import LRU
from utils.config import cache_limit
from utils.config import http_cookies
from utils.config import timeout_interval
from utils.config import tracker_url


CONTENT_PATH = "descendant-or-self::tr[contains(@class, 'hl-tr')]"

NAME_PATH = "string(descendant::td[contains(@class, 'tLeft')]/"\
    "descendant::a[contains(@class, 'tLink')])"

LINK_PATH = "string(descendant::td[contains(@class, 'small')]/"\
    "a[@title='Download' or contains(@class, 'tr-dl')]/@href)"

COOLDOWN = timeout_interval() * 10

HTTP_EXCEPTIONS = (
    ClientOSError,
    ClientPayloadError,
    InvalidURL,
    OSError,
    TimeoutError,
)


@lru_cache(maxsize=1)
def parser():
    return HTMLParser(collect_ids=False)


async def extractor(html):
    loop = get_event_loop()
    try:
        root = await loop.run_in_executor(
            None, partial(fromstring, html, parser=parser()),
        )
    except (ParserError, XMLSyntaxError):
        return

    content = root.xpath(CONTENT_PATH)
    if not content:
        await asleep(COOLDOWN)
        return

    for tag in reversed(content):
        name, link = tag.xpath(NAME_PATH), tag.xpath(LINK_PATH)
        if name and link:
            yield name.strip(), urljoin(tracker_url(), link.strip())


async def tracker(session):
    try:
        resp = await session.get(tracker_url(), allow_redirects=False)
    except HTTP_EXCEPTIONS as e:
        if isinstance(e, OSError) and e.errno != ECONNRESET:
            await aprint(f'Connection error: {str(e)}', use_stderr=True)
        return

    async with resp:
        if resp.status != 200:
            return

        try:
            html = await resp.text()
        except TimeoutError:
            return

        async for torrent_info in extractor(html):
            yield torrent_info


async def http_feed(args):
    options = dict(
        cookies=http_cookies(),
        connector=TCPConnector(
            keepalive_timeout=299,
            enable_cleanup_closed=True,
        ),
        timeout=ClientTimeout(total=timeout_interval()),
    )

    async with ClientSession(**options) as session:
        seen_urls = LRU(
            cache_limit(), [(url, None) async for _, url in tracker(session)],
        )

        if not seen_urls:
            raise SystemExit('Expired credentials')

        while True:
            async for torrent, url in tracker(session):
                if url not in seen_urls:
                    await aprint(f'{torrent}\0{url}')

            await asleep(timeout_interval())


async def _main(argv=None):
    parser = argparse.ArgumentParser(prog='torrentpier_feed')
    parser.add_argument(
        '-V',
        '--version',
        action='version',
        version='%(prog)s 0.0.1',
    )
    parser.add_argument('-u', '--url', action='store_true')
    args = parser.parse_args(argv)

    await http_feed(args)


def main():
    loop = get_event_loop()
    try:
        loop.run_until_complete(_main())
    except KeyboardInterrupt:
        raise SystemExit(130)


if __name__ == '__main__':
    exit(main())
