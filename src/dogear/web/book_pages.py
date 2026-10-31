"""The pages for books: the list and upload form, choosing what to make, a book's
progress and downloads, stopping, starting again, and deleting."""

from __future__ import annotations

from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response

from dogear.book_summary import count, describe_section
from dogear.config import Config
from dogear.errors import DogearError
from dogear.extract import format_names
from dogear.voice.cloud import CloudVoice, hosts_in_words, run_warning
from dogear.voice.engines import find_voice
from dogear.web import choices
from dogear.web.books import ID_PATTERN, MADE, BookRecord
from dogear.web.downloads import NotAvailable, downloads, media_type, output_path
from dogear.web.forms import FormTooBig, first, read_form
from dogear.web.library import CAN_CHOOSE, Library
from dogear.web.progress import LEFT_OUT_MANY, LEFT_OUT_ONE, MAX_KEPT, left_out_sentence
from dogear.web.settings import WebSettings
from dogear.web.status import Status, describe
from dogear.web.templating import message, render
from dogear.web.uploads import UploadError, disk_problem, receive_book

DELETED = "The book was deleted."
SEND = "send"  # the choose form's answer to "Send this book's text?": yes, or no
STATUS_LINES = 20  # the latest progress lines sent with each status update
# Pages for these states keep checking for news (with JavaScript) while the book stays
# in one of the states listed.
WATCH = {"reading": "reading", "waiting": "waiting making", "making": "waiting making"}


def _library(request: Request) -> Library:
    return request.app.state.library


def requested_book(request: Request) -> BookRecord:
    """The book named in the address, or a 404 page if there is none."""
    book_id = request.path_params["book_id"]
    record = _library(request).get(book_id) if ID_PATTERN.fullmatch(book_id) else None
    if record is None:
        raise HTTPException(404)
    return record


async def home(request: Request) -> Response:
    notice = DELETED if request.query_params.get("deleted") == "1" else ""
    return _home(request, notice=notice)


def _home(
    request: Request, *, errors: dict[str, str] | None = None, status_code: int = 200, notice=""
) -> Response:
    library = _library(request)
    settings: WebSettings = request.app.state.settings
    books = [
        {"id": r.id, "title": r.title, "status": _describe(library, r).sentence}
        for r in library.books()
    ]
    return render(
        request,
        "home.html",
        "Your books",
        status_code=status_code,
        errors=errors,
        book_formats=format_names("or"),
        current="books",
        books=books,
        notice=notice,
        max_upload=f"{settings.max_upload_bytes // (1024 * 1024):,} MB",
    )


async def upload(request: Request) -> Response:
    settings: WebSettings = request.app.state.settings
    try:
        received = await receive_book(request, settings.incoming_dir, settings.max_upload_bytes)
        try:
            record = await run_in_threadpool(
                _library(request).add, received.original_name, received.extension, received.path
            )
        except OSError as e:
            received.path.unlink(missing_ok=True)
            raise disk_problem(e) from None
    except UploadError as e:
        return _home(request, errors={"book": e.message}, status_code=e.status_code)
    return RedirectResponse(f"/books/{record.id}", status_code=303)


async def book_page(request: Request) -> Response:
    record = requested_book(request)
    if record.state == "ready":
        return RedirectResponse(f"/books/{record.id}/choose", status_code=303)
    library = _library(request)
    status = _describe(library, record)
    progress = library.progress(record.id)
    found = downloads(record, library.folder(record.id)) if record.state == "done" else []
    return render(
        request,
        "book.html",
        f"{status.title}: {record.title}",
        book=record,
        status=status,
        progress=progress,
        left_out=left_out_sentence(progress.left_out),
        left_out_one=LEFT_OUT_ONE,
        left_out_many=LEFT_OUT_MANY,
        max_lines=MAX_KEPT,
        show_title=record.state not in ("reading", "unreadable"),
        watching=record.state in WATCH,
        continues=WATCH.get(record.state, ""),
        downloads=found,
        made_folder=str(library.folder(record.id) / MADE),
    )


async def book_status(request: Request) -> Response:
    """The book's status as JSON, for the page's progress updates."""
    record = requested_book(request)
    library = _library(request)
    status = _describe(library, record)
    recent = library.progress(record.id).last(STATUS_LINES)
    return JSONResponse(
        {
            "state": record.state,
            "title": f"{status.title}: {record.title} - Dogear",
            "sentence": status.sentence,
            "first": recent.left_out,
            "lines": [line.shown for line in recent.lines],
        }
    )


def _describe(library: Library, record: BookRecord) -> Status:
    return describe(record, library.ahead(record.id), library.progress(record.id).latest)


async def choose_page(request: Request) -> Response:
    record = requested_book(request)
    if record.state not in CAN_CHOOSE or record.summary is None:
        return RedirectResponse(f"/books/{record.id}", status_code=303)
    config: Config = request.app.state.config
    picked = choices.defaults(record.summary, config, record.choices)
    return _choose(request, record, picked, {})


async def choose_submit(request: Request) -> Response:
    record = requested_book(request)
    if record.state not in CAN_CHOOSE or record.summary is None:
        return RedirectResponse(f"/books/{record.id}", status_code=303)
    try:
        fields = await read_form(request)
    except FormTooBig:
        return message(request, 413, "Form too big", "That form was too big", "Try again.")
    config: Config = request.app.state.config
    picked, chosen, errors = choices.check(fields, record.summary, config)
    if chosen is None:
        return _choose(request, record, picked, errors, status_code=400)
    clouds = choices.cloud_voices_for_run(chosen.voice, config)
    if clouds and first(fields, SEND) != "yes":
        if first(fields, SEND) == "no":  # "Choose another voice"
            return _choose(request, record, picked, {})
        return _send_text(request, record, picked, clouds, config)
    try:
        _library(request).choose(record.id, chosen)
    except DogearError:
        pass  # it was started from another page meanwhile; its page says how it's going
    return RedirectResponse(f"/books/{record.id}", status_code=303)


def _choose(
    request: Request,
    record: BookRecord,
    picked: choices.Picked,
    errors: dict[str, str],
    status_code: int = 200,
) -> Response:
    summary = record.summary
    assert summary is not None
    config: Config = request.app.state.config
    voices = choices.voice_options(config, summary)
    return render(
        request,
        "choose.html",
        f"Choose what to make: {summary.title}",
        status_code=status_code,
        errors=errors,
        book_id=record.id,
        summary=summary,
        about=f"{count(len(summary.sections), 'section')} and {count(summary.words, 'word')}",
        formats=choices.format_options(),
        voices=voices,
        cloud_voices=choices.offers_cloud_voices(voices),
        speeds=choices.speed_options(config),
        sections=[
            {"number": s.number, "description": describe_section(s)} for s in summary.sections
        ],
        picked=picked,
        ask_language=choices.asks_about_language(summary, config),
        language_voices=any(group.for_book_language for group in voices),
    )


def _send_text(
    request: Request,
    record: BookRecord,
    picked: choices.Picked,
    clouds: list[CloudVoice],
    config: Config,
) -> Response:
    """Before a book that may send its text off this computer starts: which voices send
    it where (every voice the run may use, not only the one chosen), in the words
    `dogear make` uses, and a button to send it, which sends the same choices again with
    send=yes."""
    summary = record.summary
    assert summary is not None
    hosts = hosts_in_words(clouds)
    main = find_voice(picked.voice, config)
    return render(
        request,
        "send_text.html",
        f"Send the text of {summary.title} to {hosts}?",
        book_id=record.id,
        summary=summary,
        hosts=hosts,
        warning=run_warning(clouds, main),
        # Listen says words with the chosen voice alone (`dogear say`).
        listen_sends=any(cloud.chosen.spec == main.spec for cloud in clouds),
        fields=choices.form_fields(picked),
        send=SEND,
    )


async def stop(request: Request) -> Response:
    record = requested_book(request)
    await run_in_threadpool(_library(request).stop, record.id)
    return RedirectResponse(f"/books/{record.id}", status_code=303)


async def read_again(request: Request) -> Response:
    record = requested_book(request)
    try:
        await run_in_threadpool(_library(request).read_again, record.id)
    except DogearError:
        pass  # being read again already, or still stopping; the book's page says which
    return RedirectResponse(f"/books/{record.id}", status_code=303)


async def start_again(request: Request) -> Response:
    record = requested_book(request)
    try:
        _library(request).start_again(record.id)
    except DogearError:
        pass  # already started again, or done; the book's page shows which
    return RedirectResponse(f"/books/{record.id}", status_code=303)


async def delete_page(request: Request) -> Response:
    record = requested_book(request)
    return render(request, "delete.html", f"Delete {record.title}?", book=record)


async def delete_submit(request: Request) -> Response:
    record = requested_book(request)
    try:
        await run_in_threadpool(_library(request).delete, record.id)
    except DogearError as e:
        return message(
            request,
            500,
            "Couldn't delete",
            "Dogear couldn't delete the book",
            *[text for text in (e.message, e.hint) if text],
        )
    return RedirectResponse("/?deleted=1", status_code=303)


async def download(request: Request) -> Response:
    record = requested_book(request)
    number = request.path_params["number"]
    if record.state != "done" or not 0 <= number < len(record.outputs):
        raise HTTPException(404)
    folder = _library(request).folder(record.id)
    try:
        path = output_path(record.outputs[number], folder)
    except NotAvailable:
        raise HTTPException(404) from None
    if path.is_dir():
        try:
            path = await run_in_threadpool(_library(request).zip, record.id, path)
        except NotAvailable:  # it's being made again
            raise HTTPException(404) from None
    return FileResponse(path, media_type=media_type(path), filename=path.name)
