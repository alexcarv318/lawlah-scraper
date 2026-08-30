import re
from datetime import date
from time import monotonic, sleep
from types import TracebackType
from typing import Self
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup, Tag
from playwright.sync_api import (
    Browser,
    Page,
    Playwright,
    ProxySettings,
    Response,
    sync_playwright,
)
from playwright.sync_api import Error as PlaywrightError

from src.legislation.schema import (
    ActListing,
    LegislationFetch,
    LegislationScrapeLimits,
    SubsidiaryListing,
    VersionListing,
)
from src.logger import get_logger
from src.raw.models.cases import FetchStatus
from src.settings import get_settings

BASE_URL = "https://sso.agc.gov.sg"

ACT_BROWSE_URL = (
    BASE_URL
    + "/Browse/Act/Current/All/{page}"
    + "?PageSize={page_size}&SortBy=Title&SortOrder=ASC"
)

SSO_HEADERS = {
    "accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
}

ASSEMBLED_CONTENT_SCRIPT = """() => {
  const hrefs = [...document.querySelectorAll('#toc a')]
    .map((anchor) => anchor.getAttribute('href') || '');
  const tocIds = hrefs
    .filter((href) => href.startsWith('#pr') || href.startsWith('#Sc'))
    .map((href) => href.slice(1));
  const presentCount = tocIds.filter((id) => document.getElementById(id)).length;
  const tocSectionCount = hrefs.filter((href) => href.startsWith('#pr')).length;
  const tocScheduleCount = hrefs.filter((href) => href.startsWith('#Sc')).length;
  const extractedSectionCount = document.querySelectorAll('#legisContent div.prov1').length;
  const extractedScheduleCount = document.querySelectorAll('#legisContent div.schedule').length;
  const loading = document.body.innerText.includes('Loading...');
  return {
    ready: !loading && tocIds.length > 0 && presentCount === tocIds.length,
    html: document.querySelector('#legisContent')?.innerHTML ?? null,
    tocSectionCount,
    tocScheduleCount,
    extractedSectionCount,
    extractedScheduleCount,
    expectedProvisionCount: tocIds.length,
    extractedProvisionCount: presentCount,
  };
}"""

SCROLL_TO_LOADED_END_SCRIPT = """() => {
  const last = [
    ...document.querySelectorAll('#legisContent div.prov1, #legisContent div.schedule'),
  ].at(-1);
  if (last) {
    last.scrollIntoView({ block: 'end' });
  }
  const root = document.scrollingElement || document.documentElement;
  window.scrollTo(0, root.scrollHeight);
}"""

logger = get_logger(__name__)


class StatutesOnlineError(Exception):
    """Singapore Statutes Online returned a page we cannot read."""


class TemporarySiteBlock(Exception):
    def __init__(self, http_status: int | None) -> None:
        super().__init__(f"Singapore Statutes Online blocked the request (HTTP {http_status})")
        self.http_status = http_status


class StatutesOnlineClient:
    def __init__(self, limits: LegislationScrapeLimits) -> None:
        settings = get_settings()
        self.playwright_proxy: ProxySettings | None = settings.playwright_proxy()
        self.limits = limits
        self.http = httpx.Client(
            headers=SSO_HEADERS,
            timeout=limits.request_timeout_seconds,
            follow_redirects=True,
        )
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.page: Page | None = None
        self.blocked_by_waf = False

        if self.playwright_proxy is not None:
            logger.info("Singapore Statutes Online traffic uses PROXY_DNS")

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._close_browser()

        if self.playwright is not None:
            self.playwright.stop()
            self.playwright = None

        self.http.close()

    def _close_browser(self) -> None:
        if self.page is not None:
            self.page.close()
            self.page = None

        if self.browser is not None:
            self.browser.close()
            self.browser = None

        self.blocked_by_waf = False

    def list_current_acts(self, max_acts: int | None = None) -> list[ActListing]:
        listings: list[ActListing] = []
        page_number = 0

        while max_acts is None or len(listings) < max_acts:
            rows = self._browse_rows(
                ACT_BROWSE_URL.format(
                    page=page_number,
                    page_size=self.limits.browse_page_size,
                ),
                required=page_number == 0,
            )
            if not rows:
                return listings

            for row in rows:
                listing = self._act_from_row(row)
                if listing is None:
                    continue

                listings.append(listing)
                if max_acts is not None and len(listings) >= max_acts:
                    return listings

            if len(rows) < self.limits.browse_page_size:
                return listings

            page_number += 1
            sleep(self.limits.browse_pause_seconds)

        return listings

    def list_versions(self, source_url: str) -> list[VersionListing]:
        html = self._load_html(source_url, "a[href*='ValidDate']:not(.file-download)")

        return self._versions_from_html(html, source_url)

    def list_subsidiary_legislations(self, act_source_url: str) -> list[SubsidiaryListing]:
        list_url = self._with_query(
            act_source_url,
            {"ViewType": "Sl", "PageSize": str(self.limits.browse_page_size)},
        )
        listings: list[SubsidiaryListing] = []

        for row in self._browse_rows(list_url, required=False):
            listing = self._subsidiary_from_row(row)
            if listing is not None:
                listings.append(listing)

        return listings

    def fetch_document(self, source_url: str) -> LegislationFetch:
        fetch_url = self._with_query(source_url, {"WholeDoc": "1"})
        attempt = 0
        block_attempt = 0

        logger.info("Fetching %s", source_url)

        while True:
            try:
                self._close_browser()
                page, http_status = self._goto(fetch_url)
                if http_status == 404:
                    return self._failed_fetch(
                        source_url,
                        FetchStatus.NOT_FOUND,
                        "Singapore Statutes Online returned 404",
                        http_status,
                    )

                self._wait_for_selector(page, "#legisContent")
                payload = self._load_whole_document(page)
                if self.blocked_by_waf:
                    raise TemporarySiteBlock(http_status)

                return self._fetch_from_page(payload, source_url, http_status)
            except TemporarySiteBlock as blocked:
                self._close_browser()
                self._wait_out_block(block_attempt, blocked.http_status)
                block_attempt += 1
            except (PlaywrightError, httpx.HTTPError) as error:
                if attempt >= self.limits.retry_limit:
                    return self._failed_fetch(source_url, FetchStatus.ERROR, str(error))

                wait_seconds = self.limits.retry_backoff_seconds * (2**attempt)
                logger.warning(
                    "Legislation fetch failed for %s, retrying in %s seconds: %s",
                    source_url,
                    wait_seconds,
                    error,
                )
                sleep(wait_seconds)
                attempt += 1

    def _browse_rows(self, url: str, required: bool) -> list[Tag]:
        html = self._load_html(url, "table.browse-list tbody")
        soup = BeautifulSoup(html, "lxml")
        table = soup.select_one("table.browse-list tbody")
        if table is None:
            if required:
                raise StatutesOnlineError(f"Browse page has no table: {url}")
            return []

        return [row for row in table.find_all("tr") if isinstance(row, Tag)]

    def _load_html(self, url: str, required_selector: str) -> str:
        block_attempt = 0

        while True:
            try:
                if self.playwright_proxy is None:
                    html = self._http_html(url, required_selector)
                    if html is not None:
                        return html

                logger.info("Loading %s in the browser", url)
                page, _status = self._goto(url)
                self._wait_for_selector(page, required_selector)
                return page.content()
            except TemporarySiteBlock as blocked:
                self._close_browser()
                self._wait_out_block(block_attempt, blocked.http_status)
                block_attempt += 1

    def _http_html(self, url: str, required_selector: str) -> str | None:
        try:
            response = self.http.get(url)
        except httpx.HTTPError as error:
            logger.warning("HTTP browse failed for %s, using the browser: %s", url, error)
            return None

        if response.status_code == 467 or (
            response.status_code == 403 and "Request blocked." in response.text
        ):
            raise TemporarySiteBlock(response.status_code)

        if not response.is_success:
            return None

        soup = BeautifulSoup(response.text, "lxml")
        if soup.select_one(required_selector) is None:
            return None

        return response.text

    def _goto(self, url: str) -> tuple[Page, int | None]:
        block_attempt = 0

        while True:
            self._close_browser()
            page = self._ensure_browser()
            response = page.goto(url, wait_until="domcontentloaded")
            http_status = response.status if response is not None else None
            if not self._is_blocked(response, page):
                return page, http_status

            self._wait_out_block(block_attempt, http_status)
            block_attempt += 1

    def _wait_for_selector(self, page: Page, selector: str) -> None:
        timeout_ms = int(self.limits.page_load_timeout_seconds * 1000)

        try:
            page.wait_for_selector(selector, state="attached", timeout=min(8000, timeout_ms))
        except PlaywrightError:
            logger.info("Page not ready, reloading")
            response = page.reload(wait_until="domcontentloaded")
            if self._is_blocked(response, page):
                raise TemporarySiteBlock(
                    response.status if response is not None else None
                )
            page.wait_for_selector(selector, state="attached", timeout=timeout_ms)

    def _ensure_browser(self) -> Page:
        if self.page is not None:
            return self.page

        if self.playwright is None:
            self.playwright = sync_playwright().start()

        self.browser = self.playwright.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
            proxy=self.playwright_proxy,
        )
        context = self.browser.new_context(
            user_agent=SSO_HEADERS["user-agent"],
            viewport={"width": 1440, "height": 900},
        )
        self.page = context.new_page()
        self.page.on("response", self._mark_http_467)

        timeout_ms = int(self.limits.page_load_timeout_seconds * 1000)
        self.page.set_default_timeout(timeout_ms)
        self.page.set_default_navigation_timeout(timeout_ms)

        return self.page

    def _mark_http_467(self, response: Response) -> None:
        if response.status == 467:
            self.blocked_by_waf = True

    def _is_blocked(self, response: Response | None, page: Page) -> bool:
        if self.blocked_by_waf or (response is not None and response.status == 467):
            self.blocked_by_waf = True
            return True

        if page.title() == "ERROR: The request could not be satisfied":
            self.blocked_by_waf = True
            return True

        return False

    def _wait_out_block(self, block_attempt: int, http_status: int | None) -> None:
        if self.playwright_proxy is not None:
            wait_seconds = 2.0
            logger.warning(
                "Singapore Statutes Online blocked the request (HTTP %s), new proxy IP in %s seconds",
                http_status,
                int(wait_seconds),
            )
        else:
            wait_seconds = min(
                self.limits.http_467_pause_seconds * (2**block_attempt),
                self.limits.http_467_pause_cap_seconds,
            )
            logger.warning(
                "Singapore Statutes Online blocked the request (HTTP %s), waiting %s minutes",
                http_status,
                int(wait_seconds // 60),
            )

        sleep(wait_seconds)

    def _load_whole_document(self, page: Page) -> dict[str, object]:
        deadline = monotonic() + self.limits.page_load_timeout_seconds
        last_log_at = 0.0
        last_extracted = -1
        last_progress_at = monotonic()
        payload: object = None

        while monotonic() < deadline:
            page.evaluate(SCROLL_TO_LOADED_END_SCRIPT)
            payload = page.evaluate(ASSEMBLED_CONTENT_SCRIPT)

            if isinstance(payload, dict) and payload.get("ready") is True:
                return payload

            extracted = 0
            expected = 0
            if isinstance(payload, dict):
                extracted = self._as_int(payload.get("extractedProvisionCount"))
                expected = self._as_int(payload.get("expectedProvisionCount"))

            now = monotonic()
            if extracted != last_extracted:
                last_extracted = extracted
                last_progress_at = now
                logger.info(
                    "Loading legislation content: %s of %s TOC items",
                    extracted,
                    expected,
                )
                last_log_at = now
            elif now - last_log_at >= 2:
                logger.info(
                    "Loading legislation content: %s of %s TOC items",
                    extracted,
                    expected,
                )
                last_log_at = now

            if (
                extracted > 0
                and now - last_progress_at >= self.limits.content_stall_seconds
                and isinstance(payload, dict)
            ):
                logger.warning(
                    "Legislation content stopped at %s of %s TOC items, saving current HTML",
                    extracted,
                    expected,
                )
                return payload

            page.wait_for_timeout(200)

        raise PlaywrightError("Timed out waiting for assembled legislation content")

    @staticmethod
    def _fetch_from_page(
        payload: dict[str, object],
        source_url: str,
        http_status: int | None,
    ) -> LegislationFetch:
        html = payload.get("html")
        toc_sections = StatutesOnlineClient._as_int(payload.get("tocSectionCount"))
        toc_schedules = StatutesOnlineClient._as_int(payload.get("tocScheduleCount"))
        extracted_sections = StatutesOnlineClient._as_int(payload.get("extractedSectionCount"))
        extracted_schedules = StatutesOnlineClient._as_int(payload.get("extractedScheduleCount"))
        expected = StatutesOnlineClient._as_int(payload.get("expectedProvisionCount"))
        extracted = StatutesOnlineClient._as_int(payload.get("extractedProvisionCount"))

        if not isinstance(html, str) or not html.strip():
            return StatutesOnlineClient._failed_fetch(
                source_url,
                FetchStatus.NOT_FOUND,
                "Page has no #legisContent",
                http_status,
            )

        logger.info("Fetched %s (%s of %s TOC items)", source_url, extracted, expected)

        return LegislationFetch(
            source_url=source_url,
            http_status=http_status,
            fetch_status=FetchStatus.SUCCESS,
            fetch_error=None,
            html=html,
            source_metadata={
                "toc_section_count": toc_sections,
                "toc_schedule_count": toc_schedules,
                "extracted_section_count": extracted_sections,
                "extracted_schedule_count": extracted_schedules,
            },
            expected_provision_count=expected,
            extracted_provision_count=extracted,
        )

    @staticmethod
    def _failed_fetch(
        source_url: str,
        fetch_status: FetchStatus,
        fetch_error: str,
        http_status: int | None = None,
    ) -> LegislationFetch:
        return LegislationFetch(
            source_url=source_url,
            http_status=http_status,
            fetch_status=fetch_status,
            fetch_error=fetch_error,
            html=None,
            source_metadata=None,
            expected_provision_count=None,
            extracted_provision_count=None,
        )

    @staticmethod
    def _act_from_row(row: Tag) -> ActListing | None:
        parsed = StatutesOnlineClient._row_link(row, "/Act/")
        if parsed is None:
            return None

        slug, title, source_url = parsed
        return ActListing(slug=slug, title=title, source_url=source_url)

    @staticmethod
    def _subsidiary_from_row(row: Tag) -> SubsidiaryListing | None:
        parsed = StatutesOnlineClient._row_link(row, "/SL/")
        if parsed is None:
            return None

        slug, title, source_url = parsed
        cells = [cell.get_text(" ", strip=True) for cell in row.find_all("td")]

        return SubsidiaryListing(
            slug=slug,
            title=title,
            number=cells[1] if len(cells) > 1 else "",
            source_url=source_url,
        )

    @staticmethod
    def _row_link(row: Tag, prefix: str) -> tuple[str, str, str] | None:
        anchor = row.select_one(f"a.non-ajax[href^='{prefix}']") or row.select_one(
            f"a[href^='{prefix}']"
        )
        if anchor is None:
            return None

        href = anchor.get("href")
        title = anchor.get_text(" ", strip=True)
        if not isinstance(href, str) or not title:
            return None

        source_url = StatutesOnlineClient._clean_url(urljoin(BASE_URL, href))
        slug = StatutesOnlineClient._path_slug(source_url, prefix)
        if slug is None:
            return None

        return slug, title, source_url

    @staticmethod
    def _versions_from_html(html: str, page_url: str) -> list[VersionListing]:
        by_date: dict[date, VersionListing] = {}
        soup = BeautifulSoup(html, "lxml")
        display_date = re.compile(r"^\d{1,2} [A-Za-z]{3} \d{4}$")

        for anchor in soup.select("a[href]"):
            href = anchor.get("href")
            if not isinstance(href, str):
                continue
            if "ViewType=Pdf" in href or "GetAmendingLegislation" in href:
                continue
            if display_date.match(anchor.get_text(" ", strip=True)) is None:
                continue

            valid_from = StatutesOnlineClient._valid_date_from_href(href)
            if valid_from is None:
                continue

            source_url = StatutesOnlineClient._clean_url(urljoin(page_url, href))
            by_date[valid_from] = VersionListing(
                valid_from=valid_from,
                is_current="/Historical/" not in source_url,
                source_url=source_url,
            )

        listings = list(by_date.values())
        current_count = sum(1 for listing in listings if listing.is_current)
        if current_count != 1 and listings:
            latest = max(listing.valid_from for listing in listings)
            listings = [
                VersionListing(
                    valid_from=listing.valid_from,
                    is_current=listing.valid_from == latest,
                    source_url=listing.source_url,
                )
                for listing in listings
            ]

        return listings

    @staticmethod
    def _with_query(url: str, extra: dict[str, str]) -> str:
        parsed = urlparse(url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query.update(extra)

        return urlunparse(parsed._replace(query=urlencode(query)))

    @staticmethod
    def _clean_url(url: str) -> str:
        parsed = urlparse(url)
        query = [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key not in {"_", "ViewType", "WholeDoc"}
        ]

        return urlunparse(parsed._replace(query=urlencode(query)))

    @staticmethod
    def _path_slug(url: str, prefix: str) -> str | None:
        path = urlparse(url).path
        if not path.startswith(prefix):
            return None

        slug = path[len(prefix) :].split("/")[0]
        return slug or None

    @staticmethod
    def _valid_date_from_href(href: str) -> date | None:
        query = dict(parse_qsl(urlparse(href).query, keep_blank_values=True))
        raw = query.get("ValidDate")
        if raw is None:
            parts = urlparse(href).path.split("/Historical/")
            raw = parts[1][:8] if len(parts) == 2 else None

        if raw is None or len(raw) != 8 or not raw.isdigit():
            return None

        return date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))

    @staticmethod
    def _as_int(value: object) -> int:
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        return 0
