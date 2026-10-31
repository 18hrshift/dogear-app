"""Rendering pages from the Jinja2 templates, with every value escaped."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup
from starlette.requests import Request
from starlette.responses import Response
from starlette.templating import Jinja2Templates

TEMPLATES = Path(__file__).parent / "templates"


# Characters HTML doesn't allow in a page: control characters other than tab, line feed,
# form feed, and carriage return; lone surrogates, which UTF-8 can't hold; and
# noncharacters. Values from a book or a form can hold any of them, and a parser may stop
# at one (libxml2 on Windows ends the page at a NUL), so each becomes U+FFFD, the
# replacement character, wherever a value is put in a page.
def _not_in_html() -> re.Pattern[str]:
    controls = "\x00-\x08\x0b\x0e-\x1f\x7f-\x9f"
    surrogates = "\ud800-\udfff"
    # U+FDD0 to U+FDEF, and the last two code points of every plane (U+FFFE, U+1FFFE...).
    noncharacters = "﷐-﷯" + "".join(
        chr(plane + 0xFFFE) + chr(plane + 0xFFFF) for plane in range(0, 0x110000, 0x10000)
    )
    return re.compile(f"[{controls}{surrogates}{noncharacters}]")


NOT_IN_HTML = _not_in_html()
REPLACEMENT = "�"


def make_templates() -> Jinja2Templates:
    environment = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=True,
        undefined=StrictUndefined,  # a missing value is a bug, not an empty string
        trim_blocks=True,
        lstrip_blocks=True,
        finalize=fit_for_html,
    )
    return Jinja2Templates(env=environment)


def fit_for_html(value: Any) -> Any:
    """`value` with every character HTML doesn't allow replaced (see NOT_IN_HTML). Jinja
    calls this on every value a template prints, before escaping it. Markup stays Markup,
    so what a macro made isn't escaped a second time."""
    if isinstance(value, Markup):
        return Markup(NOT_IN_HTML.sub(REPLACEMENT, str(value)))
    if isinstance(value, str):
        return NOT_IN_HTML.sub(REPLACEMENT, value)
    return value


def render(
    request: Request,
    template: str,
    title: str,
    *,
    status_code: int = 200,
    errors: dict[str, str] | None = None,
    current: str = "",
    **context: Any,
) -> Response:
    """A page. `title` comes before " - Dogear" in its <title>, after "Error: " when the
    page has `errors`. `current` names the navigation link for this page, if any. Every
    page's footer says whether book text can leave this computer ([voice] allow_cloud)."""
    templates: Jinja2Templates = request.app.state.templates
    shared = {
        "title": title,
        "errors": errors or {},
        "current": current,
        "cloud_allowed": request.app.state.config["voice"]["allow_cloud"],
    }
    return templates.TemplateResponse(
        request, template, {**shared, **context}, status_code=status_code
    )


def message(
    request: Request, status_code: int, title: str, heading: str, *paragraphs: str
) -> Response:
    """A page that only says something, such as "Page not found"."""
    return render(
        request,
        "message.html",
        title,
        status_code=status_code,
        heading=heading,
        paragraphs=paragraphs,
    )
