import os
import sys
import logging

SRC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
sys.path.insert(0, SRC_DIR)
os.environ.setdefault("SCRAPY_SETTINGS_MODULE", "huntloop.settings")

from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

from huntloop.spiders.greenhouse_spider import GreenhouseScraper
from huntloop.spiders.lever_spider import LeverScraper
from huntloop.settings import DATABASE_URL
from huntloop.db_models import Company
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)

# Only platforms with an implemented spider. Companies detected as some
# other platform (e.g. ashby, workday) or with ats_platform "unknown"/NULL
# are skipped, not silently dropped - see run_multi_ats_scrape() below.
SPIDERS_BY_PLATFORM = {
    "greenhouse": GreenhouseScraper,
    "lever": LeverScraper,
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
        for company in detected_rows:
            by_platform.setdefault(company.ats_platform, []).append(company.name)

        unset_platform_names = [
            company.name for company in session.query(Company).filter(Company.ats_platform.is_(None)).all()
        ]

        return by_platform, unset_platform_names
    finally:
        session.close()


def run_multi_ats_scrape():
    """Orchestrator: routes each company in `companies` to the spider for
    its detected ATS platform (companies.ats_platform, populated by
    scripts/detect_and_store_ats.py), running each implemented spider once
    with the full list of company tokens for that platform. Platforms
    without an implemented spider (e.g. ashby, workday) and companies with
    no detected platform (ats_platform "unknown" or NULL) are skipped with
    a clear log message, not silently dropped or crashed on."""
    by_platform, unset_platform_names = get_companies_by_platform()

    if unset_platform_names:
        logger.warning(
            f"Skipping {len(unset_platform_names)} companies with no detected ATS platform "
            f"(ats_platform is NULL): {unset_platform_names}"
        )

    process = CrawlerProcess(get_project_settings())
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
        process.crawl(spider_class, companies=tokens)
        scheduled_any = True

    if scheduled_any:
        process.start()
    else:
        logger.warning("No companies with an implemented spider found - nothing to scrape.")


if __name__ == '__main__':
    run_multi_ats_scrape()
