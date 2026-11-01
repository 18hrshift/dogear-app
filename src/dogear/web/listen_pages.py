"""Listen: the buttons that say a word, and the page that plays what was said.

Without JavaScript, a Listen button sends its form. The server waits a few seconds for
the recording, then shows a page with a player, a download link, and what the voice
said, or a waiting page that checks on it if it isn't ready yet. With JavaScript,
listen.js sends the same form, asking for JSON, and plays the recording in place.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Any

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response

from dogear.output.filenames import safe_filename
from dogear.sizes import describe_size
from dogear.web.books import ID_PATTERN as BOOK_ID
from dogear.web.forms import FormTooBig, first, read_form
from dogear.web.library import Library
from dogear.web.listening import ID_PATTERN, Listen, ListenError, Listens, clean_text
from dogear.web.templating import message, render

# A Listen without JavaScript waits this long for the recording before it shows the
# waiting page instead. Saying a word takes a few seconds, most of it loading the voice.
WAIT_SECONDS = 20.0
WAIT_STEP_SECONDS = 0.1
TITLE_CHARS = 60  # of the text, in the page title and the download's name
PRONUNCIATIONS = "/pronunciations"
# Pages a Listen goes back to, each with an optional fragment naming a place on it.
BACK = re.compile(r"(/pronunciations|/books/[A-Za-z0-9_-]{16}/words(/all)?)(#[a-z0-9-]{1,40})?")


@dataclass(frozen=True)
class ListenStatus:
    title: str  # the start of the page title
    heading: str
    sentence: str  # for the role="status" region


def describe(listen: Listen, ahead: int | None) -> ListenStatus:
    match listen.state:
        case "waiting":
            if not ahead:
                sentence = "Waiting: it starts in a moment."
            else:
                things = "1 other thing" if ahead == 1 else f"{ahead} other things"
                sentence = f"Waiting: Dogear is saying {things} first."
            return ListenStatus("Getting it ready", "Getting it ready", sentence)
        case "saying":
            return ListenStatus(
                "Getting it ready",
                "Getting it ready",
                "Saying it. This takes a few seconds, longer the first time.",
            )
        case "done":
            return ListenStatus("Listen", "Listen", "Ready to play.")
        case "failed":
            return ListenStatus(
                "Couldn't say it", "Dogear couldn't say it", "Dogear couldn't say it."
            )
    raise ValueError(f"unknown state: {listen.state}")


def back_to(value: str) -> str:
    """Where a Listen goes back to: one of Dogear's own pages, never anywhere else."""
    return value if BACK.fullmatch(value) else PRONUNCIATIONS


def back_label(back: str) -> str:
    return "Back to Pronunciations" if back.startswith(PRONUNCIATIONS) else "Back to the words"


def wants_json(request: Request) -> bool:
    """Whether listen.js sent this, asking for JSON instead of a page."""
    return "application/json" in request.headers.get("accept", "")


def _listens(request: Request) -> Listens:
    return request.app.state.listens


async def listen(request: Request) -> Response:
    """POST /listen: a Listen button. Its form has the `text` to say, the page to go
    `back` to, and, on a book's words page, the `book`, whose voice and speed are used."""
    try:
        fields = await read_form(request)
    except FormTooBig:
        return refused(request, "That form was too big.", 413)
    try:
        text = clean_text(first(fields, "text"))
    except ListenError as e:
        return refused(request, e.message, 400)
    options = book_voice(request.app.state.library, first(fields, "book"))
    return await start_listen(request, text, back_to(first(fields, "back")), options)


def book_voice(library: Library, book_id: str) -> tuple[str, ...]:
    """`dogear say`'s options for the voice and speed chosen for the book, if any."""
    record = library.get(book_id) if BOOK_ID.fullmatch(book_id) else None
    if record is None or record.choices is None:
        return ()
    return ("--voice", record.choices.voice, "--speed", f"{record.choices.speed:g}")


async def start_listen(
    request: Request, text: str, back: str, options: tuple[str, ...] = ()
) -> Response:
    """Queue a listen of `text` (from clean_text), and answer the form."""
    listens = _listens(request)
    try:
        new = listens.add(text, back, options)
    except ListenError as e:
        return refused(request, e.message, 503)
    if wants_json(request):
        return JSONResponse(status_json(new, listens), status_code=202)
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        found = listens.get(new.id)
        if found is None or found.settled:
            break
        await asyncio.sleep(WAIT_STEP_SECONDS)
    return RedirectResponse(f"/listen/{new.id}", status_code=303)


def refused(request: Request, why: str, status_code: int) -> Response:
    if wants_json(request):
        return JSONResponse({"state": "failed", "error": why, "hint": ""}, status_code)
    return message(request, status_code, "Couldn't listen", "Dogear can't say that", why)


async def listen_page(request: Request) -> Response:
    """GET /listen/{id}: waiting, or the player and what was said, or what went wrong."""
    listens = _listens(request)
    found = _find(request)
    if found is None:
        return message(
            request,
            404,
            "Recording gone",
            "This recording is gone",
            "Dogear keeps only the latest few recordings, and none from before it last "
            "started. Go back and choose Listen again.",
        )
    status = describe(found, listens.ahead(found.id))
    recording = listens.recording(found.id)
    return render(
        request,
        "listen.html",
        f"{status.title}: {_short(found.text)}",
        listen=found,
        status=status,
        watching=not found.settled,
        size=describe_size(recording.stat().st_size) if recording else "",
        download_name=download_name(found),
        back=found.back,
        back_label=back_label(found.back),
    )


async def listen_status(request: Request) -> Response:
    """GET /listen/{id}/status: the listen as JSON, for listen.js and progress.js."""
    return JSONResponse(status_json(_listen(request), _listens(request)))


async def recording(request: Request) -> Response:
    """GET /listen/{id}/audio: the WAV, from the listen folder only."""
    listens = _listens(request)
    found = _listen(request)
    path = listens.recording(found.id)
    if path is None:
        raise HTTPException(404)
    return FileResponse(
        path,
        media_type="audio/wav",
        filename=download_name(found),
        content_disposition_type="inline",
    )


def status_json(found: Listen, listens: Listens) -> dict[str, Any]:
    status = describe(found, listens.ahead(found.id))
    page = f"/listen/{found.id}"
    return {
        "state": found.state,
        "title": f"{status.title}: {_short(found.text)} - Dogear",
        "sentence": status.sentence,
        "first": 0,  # progress.js shows lines; a listen has none
        "lines": [],
        "page_url": page,
        "status_url": f"{page}/status",
        "audio_url": f"{page}/audio" if found.state == "done" else "",
        "text": found.text,
        "said": list(found.said),
        "error": found.error,
        "hint": found.hint,
    }


def download_name(found: Listen) -> str:
    return safe_filename(_short(found.text), ".wav", fallback="Dogear")


def _find(request: Request) -> Listen | None:
    listen_id = request.path_params["listen_id"]
    return _listens(request).get(listen_id) if ID_PATTERN.fullmatch(listen_id) else None


def _listen(request: Request) -> Listen:
    found = _find(request)
    if found is None:
        raise HTTPException(404)
    return found


def _short(text: str) -> str:
    return text if len(text) <= TITLE_CHARS else text[: TITLE_CHARS - 3].rstrip() + "..."
