"""Help, and the pages for addresses and requests Dogear doesn't have or can't answer."""

from __future__ import annotations

import logging

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import Response

from dogear.extract import format_names
from dogear.web.choices import format_options
from dogear.web.templating import message, render

log = logging.getLogger(__name__)


async def help_page(request: Request) -> Response:
    return render(
        request,
        "help.html",
        "Help",
        current="help",
        formats=format_options(),
        book_formats=format_names("and"),
    )


async def http_error(request: Request, exc: Exception) -> Response:
    status_code = exc.status_code if isinstance(exc, HTTPException) else 500
    if status_code == 404:
        return message(
            request,
            404,
            "Page not found",
            "Page not found",
            "There's nothing at this address. If it was a book, it may have been deleted.",
        )
    if status_code == 405:
        return message(
            request,
            405,
            "Not allowed",
            "That can't be done here",
            "Go back to the page you came from and use its buttons.",
        )
    return message(
        request,
        status_code,
        "Something went wrong",
        "Something went wrong",
        "Dogear couldn't answer that request. Go back and try again.",
    )


async def server_error(request: Request, exc: Exception) -> Response:
    """A bug: it is logged in Dogear's window, and the page says something went wrong."""
    log.error("A page failed: %s %s", request.method, request.url.path, exc_info=exc)
    return message(
        request,
        500,
        "Something went wrong",
        "Something went wrong",
        "Dogear hit a problem it didn't expect. Its window may say more. Go back and try again.",
    )
