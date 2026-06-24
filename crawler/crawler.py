"""A polite, resilient, recursive web crawler.

Uses requests + BeautifulSoup. Respects robots.txt, rotates User-Agents,
randomizes delay/headers to look human, retries on transient failures,
and persists its queue in the database so a run can be resumed.
"""

import random
import sqlite3
import time
import urllib.robotparser
from urllib.parse import urldefrag, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from config import CRAWL_DELAY, MAX_DEPTH, MAX_PAGES, SEED_URLS, STAY_ON_DOMAIN
from database.db import (
    add_to_queue,
    get_connection,
    get_next_url,
    mark_queue_failed,
    page_exists,
    save_page,
)

# Rotate between a handful of common, real-world browser UAs so requests
# don't all look identical to a bot-detector.
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 '
    '(KHTML, like Gecko) Version/17.4 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0',
]

SKIP_EXTENSIONS = (
    '.pdf', '.jpg', '.jpeg', '.png', '.gif', '.svg', '.zip', '.gz', '.mp4',
    '.mp3', '.avi', '.mov', '.exe', '.dmg', '.css', '.js', '.json', '.xml',
    '.woff', '.woff2', '.ttf', '.ico',
)

MAX_RETRIES = 3
REQUEST_TIMEOUT = 10


def _random_headers(referer=None):
    headers = {
        'User-Agent': random.choice(USER_AGENTS),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Connection': 'keep-alive',
    }
    if referer:
        headers['Referer'] = referer
    return headers


def normalize_url(url):
    """Make URLs consistent so we don't crawl the same page twice."""
    parsed = urlparse(url)
    url, _ = urldefrag(url)
    parsed = urlparse(url)
    normalized = parsed._replace(scheme=parsed.scheme.lower(), netloc=parsed.netloc.lower())
    url = urlunparse(normalized)
    if url.endswith('/') and url.count('/') > 2:
        url = url.rstrip('/')
    return url


# Kept as a private alias — same behavior, used internally for link discovery.
_normalize_url = normalize_url


def is_valid_url(url, seed_domain=None):
    """Return True if this URL should be crawled.

    If seed_domain is given, restricts crawling to that domain (focused crawl).
    Otherwise just filters by scheme and file type.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        return False
    if seed_domain is not None and parsed.netloc != seed_domain:
        return False
    if any(parsed.path.lower().endswith(ext) for ext in SKIP_EXTENSIONS):
        return False
    return True


# Kept as a private alias — _is_crawlable doesn't take a seed_domain.
def _is_crawlable(url):
    return is_valid_url(url)


class RobotsCache:
    """Fetches and caches robots.txt rules per domain."""

    def __init__(self):
        self._parsers = {}

    def can_fetch(self, url, user_agent='*'):
        domain = urlparse(url).netloc
        if domain not in self._parsers:
            rp = urllib.robotparser.RobotFileParser()
            robots_url = f'{urlparse(url).scheme}://{domain}/robots.txt'
            try:
                resp = requests.get(robots_url, timeout=5, headers=_random_headers())
                rp.parse(resp.text.splitlines())
            except requests.RequestException:
                rp = None  # if robots.txt is unreachable, allow by default
            self._parsers[domain] = rp
        rp = self._parsers[domain]
        if rp is None:
            return True
        return rp.can_fetch(user_agent, url)


_robots_cache = RobotsCache()


def can_crawl(url, user_agent='*'):
    """Check robots.txt (cached per domain) to see if we're allowed to crawl url."""
    return _robots_cache.can_fetch(url, user_agent=user_agent)


def fetch(url, referer=None):
    """GET a URL with retries and exponential backoff. Returns Response or None."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(
                url, headers=_random_headers(referer),
                timeout=REQUEST_TIMEOUT, allow_redirects=True,
            )
            if resp.status_code == 429 or resp.status_code >= 500:
                raise requests.RequestException(f'status {resp.status_code}')
            return resp
        except requests.RequestException as exc:
            wait = (2 ** attempt) + random.uniform(0, 1)
            print(f'  Retry {attempt}/{MAX_RETRIES} for {url} ({exc}); sleeping {wait:.1f}s')
            time.sleep(wait)
    print(f'  Giving up on {url}')
    return None


def fetch_page(url, referer=None):
    """GET a URL and parse it. Returns (status_code, soup) or (None, None) on error."""
    try:
        resp = requests.get(
            url, headers=_random_headers(referer),
            timeout=REQUEST_TIMEOUT, allow_redirects=True,
        )
        soup = BeautifulSoup(resp.text, 'html.parser')
        return resp.status_code, soup
    except requests.RequestException as exc:
        print(f'  Error fetching {url}: {exc}')
        return None, None


BODY_CHAR_LIMIT = 50_000


def extract_content(soup, base_url):
    """Extract clean text and links from a BeautifulSoup page.

    Returns a dict with title, description, body, word_count, links.
    """
    # Remove boilerplate that isn't real content.
    for tag in soup(['nav', 'footer', 'header', 'script', 'style', 'aside', 'form', 'iframe', 'noscript']):
        tag.decompose()

    # Title — fall back to the first <h1> if <title> is missing/empty.
    title = ''
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    elif soup.find('h1'):
        title = soup.find('h1').get_text(strip=True)

    # Meta description.
    description = ''
    meta = soup.find('meta', attrs={'name': 'description'})
    if meta and meta.get('content'):
        description = meta['content'].strip()

    # Body text — prefer article/main, fall back to full body.
    content_area = soup.find('article') or soup.find('main') or soup.find('body') or soup
    body = content_area.get_text(separator=' ', strip=True)
    body = body[:BODY_CHAR_LIMIT]

    # Links — absolute URLs only, deduplicated.
    links = set()
    for a in soup.find_all('a', href=True):
        absolute = normalize_url(urljoin(base_url, a['href'].strip()))
        if urlparse(absolute).scheme in ('http', 'https'):
            links.add(absolute)

    return {
        'title': title,
        'description': description,
        'body': body,
        'word_count': len(body.split()),
        'links': list(links),
    }


def extract_page(url, html):
    """Legacy tuple-returning wrapper, kept for backward compatibility."""
    soup = BeautifulSoup(html, 'html.parser')
    content = extract_content(soup, url)
    links = {link for link in content['links'] if _is_crawlable(link)}
    return content['title'], content['description'], content['body'], links


def save_links(from_url, to_urls):
    if not to_urls:
        return
    conn = get_connection()
    try:
        conn.executemany(
            'INSERT OR IGNORE INTO links (from_url, to_url) VALUES (?, ?)',
            [(from_url, to_url) for to_url in to_urls],
        )
        conn.commit()
    except sqlite3.Error as exc:
        print(f'Error saving links for {from_url}: {exc}')
    finally:
        conn.close()


def crawl(seed_urls=None, max_pages=None, max_depth=None, stay_on_domain=None):
    """Breadth-first, recursive-by-nature crawl starting from seed_urls.

    The pending/visited URL list lives in the crawl_queue DB table (via
    add_to_queue/get_next_url), so a crawl can be killed and resumed without
    re-fetching pages it already queued. Depth/referer bookkeeping is kept
    in memory since it's only needed to steer the current run.

    Resilient: failures on one URL don't stop the run, and each crawled
    page is committed to the DB immediately so progress isn't lost.
    Polite: respects robots.txt, randomizes delay and headers.
    """
    seed_urls = seed_urls or SEED_URLS
    max_pages = max_pages or MAX_PAGES
    max_depth = max_depth if max_depth is not None else MAX_DEPTH
    stay_on_domain = STAY_ON_DOMAIN if stay_on_domain is None else stay_on_domain
    seed_domain = urlparse(seed_urls[0]).netloc

    depth_of = {}
    referer_of = {}
    for url in seed_urls:
        norm = normalize_url(url)
        depth_of[norm] = 0
        add_to_queue(norm)

    pages_crawled = 0

    while pages_crawled < max_pages:
        url = get_next_url()
        if url is None:
            break  # queue exhausted

        depth = depth_of.get(url, 0)
        domain_filter = seed_domain if stay_on_domain else None

        if page_exists(url) or depth > max_depth or not is_valid_url(url, domain_filter):
            continue
        if not can_crawl(url):
            print(f'  Blocked by robots.txt: {url}')
            mark_queue_failed(url)
            continue

        print(f'[{pages_crawled + 1}/{max_pages}] Crawling (depth {depth}): {url}')

        resp = fetch(url, referer=referer_of.get(url))
        if resp is None or resp.status_code != 200:
            mark_queue_failed(url)
            continue
        content_type = resp.headers.get('Content-Type', '')
        if 'text/html' not in content_type:
            mark_queue_failed(url)
            continue

        try:
            soup = BeautifulSoup(resp.text, 'html.parser')
            content = extract_content(soup, url)
        except Exception as exc:
            print(f'  Failed to parse {url}: {exc}')
            mark_queue_failed(url)
            continue

        save_page(url, content['title'], content['description'], content['body'],
                   word_count=content['word_count'])
        save_links(url, content['links'])
        pages_crawled += 1

        if depth < max_depth:
            for link in content['links']:
                link = normalize_url(link)
                if not is_valid_url(link, domain_filter):
                    continue
                if link not in depth_of:
                    depth_of[link] = depth + 1
                    referer_of[link] = url
                    add_to_queue(link)

        # Human-like randomized delay between requests.
        time.sleep(CRAWL_DELAY + random.uniform(0, CRAWL_DELAY))

    print(f'\nDone. Crawled {pages_crawled} page(s).')
    return pages_crawled


if __name__ == '__main__':
    from database.db import init_db

    init_db()
    crawl()
