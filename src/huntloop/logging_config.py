"""
Shared logging configuration for HuntLoop.

Every module gets its logger the standard way (`logging.getLogger(__name__)`)
and relies on this module having configured the root logger first: a
consistent format (timestamp, level, module name, message), output to both
the console and a rotating file (`logs/huntloop.log`), and a level that
defaults to INFO but can be overridden via the LOG_LEVEL environment
variable (e.g. `LOG_LEVEL=WARNING`).

Call setup_logging() once, as early as possible in a process's lifetime -
a script's module level, or huntloop.settings, which is imported before
anything else when the scraper starts. It's safe to call more than once;
later calls are a no-op.
"""

import logging
import logging.handlers
import os

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_LOG_DIR = os.path.join(_REPO_ROOT, "logs")
_LOG_FILE = os.path.join(_LOG_DIR, "huntloop.log")

_MAX_BYTES = 5 * 1024 * 1024  # 5 MB per file
_BACKUP_COUNT = 3

_configured = False


def setup_logging():
    global _configured
    if _configured:
        return
    _configured = True

    os.makedirs(_LOG_DIR, exist_ok=True)

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    formatter = logging.Formatter(_LOG_FORMAT)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    file_handler = logging.handlers.RotatingFileHandler(
        _LOG_FILE, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT
    )
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
