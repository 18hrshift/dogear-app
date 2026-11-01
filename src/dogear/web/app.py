"""The web application: its routes, the checks in front of them, and its queues."""

from __future__ import annotations

import contextlib
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from dogear.config import Config
from dogear.web import (
    access_pages,
    book_pages,
    listen_pages,
    pronunciation_pages,
    site_pages,
    word_pages,
)
from dogear.web.access_key import AccessKey, StartTokens, WrongKeyDelay
from dogear.web.child import STOP_GRACE_SECONDS
from dogear.web.guard import Guard
from dogear.web.headers import SecurityHeaders
from dogear.web.library import Library
from dogear.web.listening import Listens
from dogear.web.page_words import PageWords
from dogear.web.settings import WebSettings
from dogear.web.templating import make_templates

STATIC = Path(__file__).parent / "static"
BOOK = "/books/{book_id}"
LISTEN = "/listen/{listen_id}"


def create_app(
    settings: WebSettings,
    config: Config,
    key: AccessKey,
    library: Library | None = None,
    listens: Listens | None = None,
) -> Starlette:
    """The app. Its queues (books, and Listen) start with the app (its lifespan) and stop
    with it."""
    library = library or Library(settings)
    listens = listens or Listens(settings)
    # Uploads left half-written by an earlier run.
    shutil.rmtree(settings.incoming_dir, ignore_errors=True)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        library.start()
        listens.start()
        try:
            yield
        finally:
            await run_in_threadpool(listens.close, STOP_GRACE_SECONDS + 5)
            await run_in_threadpool(library.close)

    routes = [
        Route("/", book_pages.home),
        Route("/books", book_pages.upload, methods=["POST"]),
        Route(BOOK, book_pages.book_page),
        Route(BOOK + "/status", book_pages.book_status),
        Route(BOOK + "/choose", book_pages.choose_page),
        Route(BOOK + "/choose", book_pages.choose_submit, methods=["POST"]),
        Route(BOOK + "/stop", book_pages.stop, methods=["POST"]),
        Route(BOOK + "/again", book_pages.start_again, methods=["POST"]),
        Route(BOOK + "/read", book_pages.read_again, methods=["POST"]),
        Route(BOOK + "/delete", book_pages.delete_page),
        Route(BOOK + "/delete", book_pages.delete_submit, methods=["POST"]),
        Route(BOOK + "/files/{number:int}", book_pages.download),
        Route(BOOK + "/words", word_pages.words_page),
        Route(BOOK + "/words", word_pages.save_words, methods=["POST"]),
        Route(BOOK + "/words/all", word_pages.all_words_page),
        Route(BOOK + "/words/all", word_pages.save_all_words, methods=["POST"]),
        Route(BOOK + "/words/check", word_pages.start_check, methods=["POST"]),
        Route(BOOK + "/words/status", word_pages.words_status),
        Route("/pronunciations", pronunciation_pages.pronunciations_page),
        Route("/pronunciations", pronunciation_pages.save, methods=["POST"]),
        Route("/pronunciations/add", pronunciation_pages.add, methods=["POST"]),
        Route("/pronunciations/try", pronunciation_pages.try_saying, methods=["POST"]),
        Route("/listen", listen_pages.listen, methods=["POST"]),
        Route(LISTEN, listen_pages.listen_page),
        Route(LISTEN + "/status", listen_pages.listen_status),
        Route(LISTEN + "/audio", listen_pages.recording),
        Route("/start", access_pages.start),
        Route("/access", access_pages.access, methods=["POST"]),
        Route("/help", site_pages.help_page),
        Mount("/static", StaticFiles(directory=STATIC), name="static"),
    ]
    app = Starlette(
        routes=routes,
        middleware=[
            Middleware(SecurityHeaders),
            Middleware(Guard, settings=settings, key=key),
        ],
        exception_handlers={
            404: site_pages.http_error,
            405: site_pages.http_error,
            Exception: site_pages.server_error,
        },
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.config = config
    app.state.key = key
    app.state.library = library
    app.state.page_words = PageWords(settings.words_file, settings.word_limits)
    app.state.listens = listens
    app.state.templates = make_templates()
    app.state.wrong_keys = WrongKeyDelay()
    app.state.start_tokens = StartTokens()
    return app
