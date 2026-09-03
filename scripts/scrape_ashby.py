"""
Run ONLY the Ashby spider - for proving/re-running it without a full
main.py crawl. Same pipeline (JobDataPipeline), same relevance
classification + embedding at insert time, same metrics.

  PYTHONPATH=src python scripts/scrape_ashby.py            # all ashby companies
  PYTHONPATH=src python scripts/scrape_ashby.py notion     # just these

Companies are read from the `companies` table (ats_platform='ashby');
`ats_token` is the Ashby jobBoardName slug. main.py routes the same rows
the same way in the daily run - this is just a scoped entrypoint.
"""
import os
import sys

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC)
os.environ.setdefault("SCRAPY_SETTINGS_MODULE", "huntloop.settings")

from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from huntloop.db_models import Company
from huntloop.settings import DATABASE_URL
from huntloop.spiders.ashby_spider import AshbyScraper


def main():
    wanted = set(sys.argv[1:])
    session = sessionmaker(bind=create_engine(DATABASE_URL))()
    rows = session.query(Company).filter(Company.ats_platform == "ashby").all()
    session.close()

    slugs = [c.ats_token or c.name for c in rows if not wanted or c.name in wanted or c.ats_token in wanted]
    if not slugs:
        print(f"No matching ashby companies (wanted={wanted or 'ALL'})")
        return
    print(f"Scraping {len(slugs)} Ashby companies: {sorted(slugs)}")

    process = CrawlerProcess(get_project_settings())
    process.crawl(AshbyScraper, companies=slugs)
    process.start()


if __name__ == "__main__":
    main()
