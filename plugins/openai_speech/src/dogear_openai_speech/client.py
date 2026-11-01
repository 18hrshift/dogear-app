"""Asking the service for one sentence's audio: `POST <base_url>/audio/speech`, with
Python's own urllib.

The body is JSON: `model`, `voice`, `input` (the sentence), `response_format`, `speed`,
and `instructions` when the settings give them. The answer is the audio itself; an error
is an HTTP status, usually with a JSON body saying why.

A failure that may pass (asked to slow down, a server error, a lost connection or a
timeout) is tried again, waiting longer each time, or as long as the service says, and
every request waits its turn under `requests_per_minute`. Anything else becomes a
VoiceError at once, with a hint. Redirects aren't followed, so the text goes nowhere but
the address in the settings, and a server on this computer is reached directly, never
through a proxy that the environment names. No error from here repeats the key
(key_text.py).
"""

from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from importlib import metadata
from typing import Any

from dogear.errors import DogearError, VoiceError

from dogear_openai_speech.address import TABLE
from dogear_openai_speech.key_text import hide_key
from dogear_openai_speech.settings import Settings

Report = Callable[[str], None]

MAX_WAIT_SECONDS = 120.0  # the longest wait between tries, whatever the service asks
MAX_ERROR_BYTES = 64 * 1024  # of an error's body, read for the service's reason
MAX_REASON_CHARS = 300
TRY_AGAIN = {408, 425, 429, 500, 502, 503, 504}
KEPT = "The sentences spoken so far are kept."


@dataclass(frozen=True)
class Answer:
    audio: bytes
    content_type: str


class _Passing(Exception):
    """A failure that may pass if the request is tried again."""

    def __init__(self, error: VoiceError, why: str, wait: float | None = None) -> None:
        super().__init__(error.message)
        self.error = error
        self.why = why  # "it asked Dogear to slow down", for the progress line
        self.wait = wait  # what the service asked for, in seconds, if anything


class ClientBug(Exception):
    """An error Dogear didn't expect, from this code or Python's, whose message had the
    key in it: the same message with the key taken out. Dogear reports it as a bug in
    the plugin."""


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # urllib then raises the 3xx as an HTTPError


class SpeechClient:
    def __init__(
        self,
        settings: Settings,
        key: str,
        report: Report,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self._key = key
        self._report = report
        self._sleep = sleep
        self._clock = clock
        self._next_turn = 0.0
        # Straight to a server on this computer; through the environment's proxy otherwise.
        proxies = urllib.request.ProxyHandler({} if settings.address.local else None)
        self._opener = urllib.request.build_opener(proxies, _NoRedirects())

    def speak(self, voice: str, text: str, speed: float, most_bytes: int) -> Answer:
        """The audio for `text`, at most `most_bytes` of it. Raises VoiceError. No error
        leaves here with the key in it, whatever raised it (key_text.hide_key)."""
        try:
            return self._speak(voice, text, speed, most_bytes)
        except DogearError as e:
            raise type(e)(self._hidden(e.message), self._hidden(e.hint)) from None
        except Exception as e:
            if any(self._hidden(str(link)) != str(link) for link in _chain(e)):
                # Raised again without the key, and without the errors it came from.
                raise ClientBug(f"{type(e).__name__}: {self._hidden(str(e))}") from None
            raise  # nothing of the key in it: as it was, for its traceback

    def _hidden(self, text: str) -> str:
        return hide_key(text, self._key)

    def _speak(self, voice: str, text: str, speed: float, most_bytes: int) -> Answer:
        body = {
            "model": self.settings.model,
            "voice": voice,
            "input": text,
            "response_format": self.settings.response_format,
            "speed": speed,
        }
        if self.settings.instructions:
            body["instructions"] = self.settings.instructions
        data = json.dumps(body).encode("utf-8")
        tries = self.settings.retries + 1
        for attempt in range(1, tries + 1):
            self._wait_for_turn()
            try:
                return self._send(data, most_bytes)
            except _Passing as failure:
                if attempt == tries:
                    raise failure.error from None
                wait = failure.wait
                if wait is None:
                    wait = self.settings.retry_wait_seconds * 2 ** (attempt - 1)
                wait = min(wait, MAX_WAIT_SECONDS)
                self._report(
                    f"{self.settings.address.host} didn't speak a sentence: {failure.why}. "
                    f"Trying again in {_seconds(wait)}, which is try {attempt + 1} of {tries}."
                )
                self._sleep(wait)
        raise AssertionError("unreachable")

    def _wait_for_turn(self) -> None:
        per_minute = self.settings.requests_per_minute
        if not per_minute:
            return
        now = self._clock()
        if now < self._next_turn:
            self._sleep(self._next_turn - now)
            now = self._next_turn
        self._next_turn = now + 60.0 / per_minute

    def _send(self, data: bytes, most_bytes: int) -> Answer:
        request = urllib.request.Request(
            self.settings.address.speech_url, data=data, method="POST", headers=self._headers()
        )
        try:
            with self._opener.open(request, timeout=self.settings.timeout_seconds) as response:
                audio = response.read(most_bytes + 1)
                content_type = response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            with e:
                raise self._status_error(e) from None
        except TimeoutError:
            raise _Passing(self._timed_out(), "it didn't answer in time") from None
        except (urllib.error.URLError, http.client.HTTPException, OSError) as e:
            reason = getattr(e, "reason", None) or e
            raise _Passing(self._unreachable(reason), "the connection failed") from None
        except ValueError:
            # Python refuses a header it can't send, and its message repeats the header.
            raise self._unsendable() from None
        if len(audio) > most_bytes:
            raise VoiceError(
                f"{self.settings.address.host} sent back more than a sentence's audio could "
                "be, so Dogear stopped reading it.",
                hint=f"Check model and response_format under [{TABLE}].",
            )
        kind = content_type.split(";")[0].strip().lower()
        if kind.startswith("text/") or kind in ("application/json", "application/problem+json"):
            reason = _reason(audio, self._key)
            raise VoiceError(
                f"{self.settings.address.host} sent back {kind}, not audio"
                + (f": {reason}" if reason else "."),
                hint=f"Check base_url, model, and voices under [{TABLE}].",
            )
        return Answer(audio, kind)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "audio/*, application/octet-stream",
            "User-Agent": f"dogear-openai-speech/{_version()}",
        }
        if self._key:
            headers["Authorization"] = f"Bearer {self._key}"
        return headers

    # Errors, in words.

    def _status_error(self, e: urllib.error.HTTPError) -> VoiceError | _Passing:
        host, code = self.settings.address.host, e.code
        body = e.read(MAX_ERROR_BYTES) if e.fp is not None else b""
        reason = _reason(body, self._key)
        said = f": {reason}" if reason else "."
        if 300 <= code < 400:
            elsewhere = e.headers.get("Location") or "an address it didn't give"
            return VoiceError(
                f"{host} sent the request somewhere else ({code}, to {elsewhere}), and "
                "Dogear sends the text only to the address in its settings.",
                hint=f"If that address is right, set base_url under [{TABLE}] to it.",
            )
        if code == 401:
            # Not the service's reason: it may repeat part of the key.
            return VoiceError(
                f"{host} turned down the key (401 Unauthorized).",
                hint=self._key_hint(),
            )
        if code == 403:
            return VoiceError(
                f"{host} refused the request (403 Forbidden){said}",
                hint=f"The key may not be allowed to use the model {self.settings.model}. "
                "Check your account with the service.",
            )
        if code == 404:
            return VoiceError(
                f"{host} has no speech service at {self.settings.address.speech_url} "
                f"(404 Not Found){said}",
                hint=f"Check base_url and model under [{TABLE}]. Most services' base_url "
                "ends in /v1.",
            )
        if code == 413:
            return VoiceError(
                f"{host} says a sentence is too long (413){said}",
                hint=f"Lower max_input_chars under [{TABLE}] and max_sentence_chars under "
                "[speech], to what the service takes.",
            )
        if code == 429 and _code(body) == "insufficient_quota":
            return VoiceError(
                f"{host} says the account has run out of credit (429){said}",
                hint=f"Add credit with the service, then run the same command again. {KEPT}",
            )
        if code in TRY_AGAIN:
            slow = code == 429
            error = VoiceError(
                f"{host} asked Dogear to slow down (429 Too Many Requests), and still did "
                f"after {self.settings.retries + 1} tries{said}"
                if slow
                else f"{host} had a problem of its own ({code} {e.reason}), and still did after "
                f"{self.settings.retries + 1} tries{said}",
                hint=f"Lower requests_per_minute under [{TABLE}], or wait a while, then run "
                f"the same command again. {KEPT}"
                if slow
                else f"Try again later: run the same command again. {KEPT}",
            )
            why = "it asked Dogear to slow down" if slow else f"it had a problem ({code})"
            return _Passing(error, why, _retry_after(e.headers.get("Retry-After")))
        return VoiceError(
            f"{host} turned down the request ({code} {e.reason}){said}",
            hint=f"Check model, voices, response_format, and instructions under [{TABLE}] "
            "against what the service takes.",
        )

    def _key_hint(self) -> str:
        name = self.settings.api_key_env
        if not name:
            return (
                f"It wants a key. Set api_key_env under [{TABLE}] to the name of an "
                "environment variable, and put your key for the service in it."
            )
        if not self._key:
            return f"It wants a key: put your key for the service in {name}."
        return f"Check the key in {name}: it must be your key for this service."

    def _unsendable(self) -> VoiceError:
        """Python wouldn't send a header. Only the key comes from outside the plugin, and
        settings.api_key checks it, so this is a key that got past the check."""
        name = self.settings.api_key_env or "the environment variable api_key_env names"
        return VoiceError(
            f"Dogear couldn't send the request to {self.settings.address.host}: a header "
            "in it has a character a request can't carry, usually in the key.",
            hint=f"Set {name} again to the key alone, copied from the service, on one line.",
        )

    def _timed_out(self) -> VoiceError:
        seconds = _seconds(self.settings.timeout_seconds)
        return VoiceError(
            f"{self.settings.address.host} didn't answer within {seconds}, after "
            f"{self.settings.retries + 1} tries.",
            hint=f"Try again later, or raise timeout_seconds under [{TABLE}]. {KEPT}",
        )

    def _unreachable(self, reason: object) -> VoiceError:
        address = self.settings.address
        where = (
            f"Check that the server is running at {address.base_url}"
            if address.local
            else "Check the internet connection"
        )
        return VoiceError(
            f"Dogear couldn't reach {address.host}: {reason}.",
            hint=f"{where}, and base_url under [{TABLE}], then run the same command again. {KEPT}",
        )


def _reason(body: bytes, key: str) -> str:
    """The service's own reason for an error, on one line, without the key in it."""
    text = body.decode("utf-8", errors="replace")
    try:
        found: Any = json.loads(text)
    except ValueError:
        found = text
    for name in ("error", "message", "detail"):
        if isinstance(found, dict) and name in found:
            found = found[name]
    if isinstance(found, dict):
        found = found.get("message", "")
    reason = hide_key(" ".join(hide_key(str(found), key).split()), key)
    if reason.lstrip().startswith("<"):
        return ""  # a web page, not a reason
    return reason[:MAX_REASON_CHARS].rstrip(".") + "." if reason else ""


def _chain(error: BaseException) -> list[BaseException]:
    """`error` and every error it was raised from or while handling, as a traceback
    shows them."""
    found: list[BaseException] = []
    link: BaseException | None = error
    while link is not None and link not in found:
        found.append(link)
        link = link.__cause__ or link.__context__
    return found


def _code(body: bytes) -> str:
    try:
        error = json.loads(body.decode("utf-8", errors="replace")).get("error", {})
    except (ValueError, AttributeError):
        return ""
    return str(error.get("code") or error.get("type") or "") if isinstance(error, dict) else ""


def _retry_after(value: str | None) -> float | None:
    """The seconds a Retry-After header asks for, if it gives a number of them."""
    try:
        seconds = float(value) if value else None
    except ValueError:
        return None  # an HTTP date: wait the usual time instead
    return seconds if seconds is not None and seconds >= 0 else None


def _seconds(value: float) -> str:
    whole = round(value)
    return "1 second" if whole == 1 else f"{whole:,} seconds"


def _version() -> str:
    try:
        return metadata.version("dogear-openai-speech")
    except metadata.PackageNotFoundError:
        return "0"
