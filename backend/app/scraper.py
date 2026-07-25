import asyncio
import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from .config import Settings
from .content_cleaner import clean_markdown
from .security import UnsafeUrl, normalize_approved_url

SUSPICIOUS = re.compile(
    r"ignore (?:all |the )?(?:previous|prior) instructions|system prompt|developer message|"
    r"execute (?:this|the) command|reveal (?:secrets|credentials)",
    re.IGNORECASE,
)
MARKDOWN_LINK = re.compile(r"\[[^\]]*]\((https?://[^)\s]+|/[^)\s]+)\)")
BALANCED_SEEDS = (
    "https://official.nba.com/rule-no-3-players-substitutes-and-coaches/",
    "https://www.nba.com/news/about",
    "https://www.nba.com/teams",
    "https://www.nba.com/players",
    "https://www.nba.com/standings",
    "https://www.nba.com/schedule",
    "https://www.nba.com/stats/help/glossary",
    "https://www.nba.com/stats/history",
    "https://www.nba.com/history",
    "https://www.nba.com/news",
    "https://www.nba.com/",
)
PRIORITY_PATHS = (
    "/rule-no-", "/rulebook", "/teams", "/team/", "/players", "/player/", "/standings", "/schedule",
    "/stats/help/", "/stats/history", "/history", "/news",
)


class NbaScraper:
    """Bounded NBA crawler that persists ScraperAPI's Markdown response."""

    def __init__(self, config: Settings):
        self.config = config

    async def _robots(self, client: httpx.AsyncClient, seed_url: str) -> RobotFileParser:
        parsed = urlparse(seed_url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        response = await client.get(robots_url)
        response.raise_for_status()
        parser = RobotFileParser()
        parser.set_url(robots_url)
        parser.parse(response.text.splitlines())
        return parser

    async def _download_markdown(self, client: httpx.AsyncClient, target_url: str) -> str:
        if not self.config.use_scraperapi:
            raise ValueError("Markdown ingestion requires USE_SCRAPERAPI=true.")
        if not self.config.scraperapi_key:
            raise ValueError("SCRAPERAPI_KEY is missing from the local environment.")
        response = await client.get(
            "https://api.scraperapi.com",
            params={
                "api_key": self.config.scraperapi_key,
                "url": target_url,
                "output_format": "markdown",
                "render": str(self.config.scraperapi_render).lower(),
            },
        )
        response.raise_for_status()
        if len(response.content) > self.config.crawl_max_bytes:
            raise ValueError("Markdown response exceeded the size limit.")
        return response.content.decode("utf-8", errors="replace")

    @staticmethod
    def _link_priority(url: str) -> tuple[int, str]:
        path = urlparse(url).path.lower()
        priority = next((index for index, prefix in enumerate(PRIORITY_PATHS) if path.startswith(prefix)), 99)
        return priority, url

    def _links(self, base_url: str, markdown: str) -> list[str]:
        links: set[str] = set()
        for match in MARKDOWN_LINK.finditer(markdown):
            try:
                link = normalize_approved_url(urljoin(base_url, match.group(1)), self.config.allowed_hosts)
            except UnsafeUrl:
                continue
            path = urlparse(link).path
            if re.search(r"\.(jpg|jpeg|png|gif|svg|mp4|pdf)$", path, re.IGNORECASE):
                continue
            links.add(link)
        return sorted(links, key=self._link_priority)

    @staticmethod
    def _title(markdown: str, fallback: str) -> str:
        heading = re.search(r"^\s*#\s+(.+?)\s*$", clean_markdown(markdown), re.MULTILINE)
        return (heading.group(1) if heading else fallback)[:300]

    def _page(self, url: str, markdown: str) -> dict:
        if len(markdown) < 100:
            raise ValueError("Markdown response contained too little useful content.")
        if SUSPICIOUS.search(markdown):
            raise ValueError("Prompt-injection-like content was quarantined.")
        return {
            "url": url,
            "title": self._title(markdown, url),
            "retrieved_at": datetime.now(UTC).isoformat(),
            "content_hash": hashlib.sha256(markdown.encode()).hexdigest(),
            "text": markdown,
        }

    def persist_markdown(self, pages: list[dict]) -> None:
        directory = self.config.markdown_dir
        directory.mkdir(parents=True, exist_ok=True)
        for page in pages:
            digest = hashlib.sha256(page["url"].encode()).hexdigest()[:14]
            path_slug = re.sub(r"[^a-z0-9]+", "-", urlparse(page["url"]).path.lower()).strip("-")[:65] or "home"
            target = directory / f"{path_slug}-{digest}.md"
            frontmatter = (
                "---\n"
                f"title: {page['title'].replace(chr(10), ' ')}\n"
                f"source_url: {page['url']}\n"
                f"retrieved_at: {page['retrieved_at']}\n"
                f"content_sha256: {page['content_hash']}\n"
                "---\n\n"
            )
            target.write_text(frontmatter + page["text"], encoding="utf-8")

    async def fetch_page(self, target_url: str) -> dict:
        """Fetch and persist one approved page for incremental ingestion."""
        target = normalize_approved_url(target_url, self.config.allowed_hosts)
        headers = {"User-Agent": self.config.crawler_user_agent}
        timeout = httpx.Timeout(max(70, self.config.crawl_timeout_seconds))
        async with httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=False) as client:
            robots = await self._robots(client, target)
            if not robots.can_fetch(self.config.crawler_user_agent, target):
                raise ValueError(f"robots.txt denied {target}")
            markdown = (await self._download_markdown(client, target)).strip()
        page = self._page(target, markdown)
        self.persist_markdown([page])
        return page

    async def crawl(self, seed_url: str, max_pages: int) -> tuple[list[dict], list[str]]:
        normalize_approved_url(seed_url, self.config.allowed_hosts)
        headers = {"User-Agent": self.config.crawler_user_agent}
        timeout = httpx.Timeout(max(70, self.config.crawl_timeout_seconds))
        pages: list[dict] = []
        errors: list[str] = []
        queue = list(dict.fromkeys(BALANCED_SEEDS))
        seen: set[str] = set()

        async with httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=False) as client:
            robots_by_origin: dict[str, RobotFileParser] = {}
            while queue and len(pages) < max_pages:
                candidate = queue.pop(0)
                if candidate in seen:
                    continue
                seen.add(candidate)
                parsed_candidate = urlparse(candidate)
                origin = f"{parsed_candidate.scheme}://{parsed_candidate.netloc}"
                if origin not in robots_by_origin:
                    robots_by_origin[origin] = await self._robots(client, candidate)
                if not robots_by_origin[origin].can_fetch(self.config.crawler_user_agent, candidate):
                    errors.append(f"robots.txt denied {candidate}")
                    continue
                try:
                    markdown = (await self._download_markdown(client, candidate)).strip()
                    pages.append(self._page(candidate, markdown))
                    discovered = self._links(candidate, markdown)
                    queue.extend(link for link in discovered if link not in seen and link not in queue)
                    queue.sort(key=self._link_priority)
                except (httpx.HTTPError, ValueError, UnsafeUrl) as exc:
                    errors.append(f"{candidate}: {exc}")
                if queue and len(pages) < max_pages:
                    await asyncio.sleep(self.config.crawl_delay_seconds)
        self.persist_markdown(pages)
        return pages, errors
