import os
import sys

SRC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
sys.path.insert(0, SRC_DIR)
os.environ.setdefault("SCRAPY_SETTINGS_MODULE", "huntloop.settings")

from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings
from huntloop.spiders.greenhouse_spider import GreenhouseScraper


def run_greenhouse_scraper():
    process = CrawlerProcess(get_project_settings())
    process.crawl(GreenhouseScraper)
    process.start()


if __name__ == '__main__':
    run_greenhouse_scraper()
