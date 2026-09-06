import os
import sys
import logging
import time

SRC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
sys.path.insert(0, SRC_DIR)
os.environ.setdefault("SCRAPY_SETTINGS_MODULE", "huntloop.settings")

from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

from huntloop.spiders.greenhouse_spider import GreenhouseScraper
from huntloop.spiders.lever_spider import LeverScraper
from huntloop.spiders.workday_spider import WorkdayScraper
from huntloop.spiders.smartrecruiters_spider import SmartRecruitersScraper
from huntloop.spiders.ashby_spider import AshbyScraper
from huntloop.spiders.icims_spider import IcimsScraper
from huntloop.spiders.gem_spider import GemScraper
from huntloop.settings import DATABASE_URL
from huntloop.db_models import Company
from huntloop import metrics
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)

# Only platforms with an implemented spider. Companies detected as some
# other platform (e.g. an unimplemented one) or with ats_platform "unknown"/NULL
# are skipped, not silently dropped - see run_multi_ats_scrape() below.
SPIDERS_BY_PLATFORM = {
    "greenhouse": GreenhouseScraper,
    "lever": LeverScraper,
    "workday": WorkdayScraper,
    "smartrecruiters": SmartRecruitersScraper,
    "ashby": AshbyScraper,
    "icims": IcimsScraper,
    "gem": GemScraper,
}


def get_companies_by_platform():
    """Query `companies` for detected ATS platforms and group company
    tokens by platform. Returns (by_platform, unset_platform_names) -
    by_platform excludes rows where ats_platform is NULL (never
    successfully detected - see scripts/detect_and_store_ats.py);
    unset_platform_names lists those separately, so the caller can log
    them rather than silently drop them."""
    engine = create_engine(DATABASE_URL, echo=False)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        detected_rows = session.query(Company).filter(Company.ats_platform.isnot(None)).all()
        by_platform = {}
        careers_urls = {}
        for company in detected_rows:
            by_platform.setdefault(company.ats_platform, []).append(company.name)
            if company.careers_url:
                careers_urls[company.name] = company.careers_url

        unset_platform_names = [
            company.name for company in session.query(Company).filter(Company.ats_platform.is_(None)).all()
        ]

        return by_platform, unset_platform_names, careers_urls
    finally:
        session.close()


def run_multi_ats_scrape():
    """Orchestrator: routes each company in `companies` to the spider for
    its detected ATS platform (companies.ats_platform, populated by
    scripts/detect_and_store_ats.py), running each implemented spider once
    with the full list of company tokens for that platform. Platforms
    without an implemented spider and companies with
    no detected platform (ats_platform "unknown" or NULL) are skipped with
    a clear log message, not silently dropped or crashed on. At the end
    of the run (success or failure), this run's metrics (jobs scraped/
    inserted/skipped/errored per company+source, plus run duration) are
    pushed to the Pushgateway as a single batch - see huntloop.metrics."""
    start_time = time.perf_counter()
    try:
        by_platform, unset_platform_names, careers_urls = get_companies_by_platform()

        if unset_platform_names:
            logger.warning(
                f"Skipping {len(unset_platform_names)} companies with no detected ATS platform "
                f"(ats_platform is NULL): {unset_platform_names}"
            )

        process = CrawlerProcess(get_project_settings())
        # CrawlerProcess.__init__ calls Scrapy's configure_logging(), which
        # unconditionally runs dictConfig(DEFAULT_LOGGING) and pins the
        # "scrapy" logger to DEBUG - regardless of settings.py's
        # LOG_ENABLED = False (that only swaps Scrapy's own root handler for
        # a NullHandler, it doesn't lower the logger level). At DEBUG,
        # scrapy.core.scraper logs a full pprint dump of every scraped item
        # - including the entire job_description HTML - through
        # huntloop.logging_config's root handlers: ~164 lines per posting,
        # ~85k postings per scheduled run, which is what made cron.log grow
        # to multiple GB per run (see the 2026-09-06 log-verbosity
        # investigation - the item-echo DEBUG dump was the cause, not the
        # skills-matching stage). Raising it to INFO here - AFTER the
        # constructor, since configure_logging() has already run by now -
        # drops the per-item dump and the per-request "Crawled (200)" trace
        # while keeping every Scrapy WARNING/ERROR, the end-of-crawl stats
        # block, and huntloop.pipelines' own per-posting INFO lines.
        logging.getLogger("scrapy").setLevel(logging.INFO)
        scheduled_any = False

        for platform in sorted(by_platform):
            tokens = by_platform[platform]
            spider_class = SPIDERS_BY_PLATFORM.get(platform)
            if spider_class is None:
                logger.warning(
                    f"No spider implemented for platform '{platform}', skipping {len(tokens)} companies: {tokens}"
                )
                continue

            logger.info(f"Running {spider_class.name} for {len(tokens)} companies: {tokens}")
            if platform == "workday":
                # Workday needs each company's {tenant, dc, site}, stored
                # in companies.careers_url (see huntloop.workday_url).
                # Companies routed here without one (e.g. ambiguous
                # generic-slug tenants flagged for review by
                # scripts/discover_and_store_workday.py) are skipped by
                # the spider, not guessed at.
                wd_urls = {name: careers_urls[name] for name in tokens if name in careers_urls}
                missing = [name for name in tokens if name not in wd_urls]
                if missing:
                    logger.warning(
                        f"Workday: {len(missing)} companies have ats_platform='workday' but no "
                        f"careers_url - skipping: {missing}"
                    )
                if not wd_urls:
                    continue
                process.crawl(spider_class, companies=list(wd_urls), careers_urls=wd_urls)
            else:
                process.crawl(spider_class, companies=tokens)
            scheduled_any = True

        if scheduled_any:
            process.start()
        else:
            logger.warning("No companies with an implemented spider found - nothing to scrape.")
    finally:
        metrics.run_duration_seconds.set(time.perf_counter() - start_time)
        metrics.push_run_metrics()


if __name__ == '__main__':
    run_multi_ats_scrape()
