import os
import sys
import pathlib
import logging.handlers
import types

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class _FakeRotatingHandler(logging.NullHandler):
    def __init__(self, *args, **kwargs):
        super().__init__()

    def setFormatter(self, *args, **kwargs):
        pass


_original_handlers = {}


def pytest_configure(config):
    os.environ.setdefault("NO_PROXY", "*")
    _original_handlers["RotatingFileHandler"] = logging.handlers.RotatingFileHandler
    logging.handlers.RotatingFileHandler = _FakeRotatingHandler
    os.environ["USERBOT_ANTI_SPAM_ENABLED"] = "0"


def pytest_unconfigure(config):
    if "RotatingFileHandler" in _original_handlers:
        logging.handlers.RotatingFileHandler = _original_handlers["RotatingFileHandler"]


@pytest.fixture()
def fresh_db(tmp_path):
    """Route userbot_db to a brand-new temp SQLite database."""
    from Shared import userbot_db

    original = userbot_db.DB_PATH
    db_file = tmp_path / "test_userbot.db"
    userbot_db.DB_PATH = db_file
    userbot_db.init_db()
    yield userbot_db
    userbot_db.DB_PATH = original


@pytest.fixture(scope="session")
def userbot_main():
    """Import UserBot.main once (after log-handler stub is in place)."""
    import UserBot.main as main

    return main
