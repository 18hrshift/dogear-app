"""Voices that send text off this computer ("cloud voices"): which they are, refusing them
unless the user allows it, and saying plainly what goes where before anything is sent.

Dogear's core enforces this, not each engine. An engine only says where its voice sends
text with the settings it is given (`sends_text_to`); a server on this computer counts as
staying here. The user allows cloud voices with `[voice] allow_cloud`, or for one run with
--allow-cloud. Every run that speaks makes its voice through `make_engine`, which refuses
or warns before the engine is even made; `dogear make` also asks before it reads the book,
so a refusal comes at once.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from dogear.config import Config
from dogear.errors import VoiceError
from dogear.voice.engines import ChosenVoice, run_plugin

ALLOW_OPTION = "--allow-cloud"  # `dogear make` and `dogear say`, for one run


@dataclass(frozen=True)
class CloudVoice:
    """A voice that sends the text it speaks to another computer."""

    chosen: ChosenVoice
    host: str  # where the text goes, such as "api.openai.com"


def text_goes_to(chosen: ChosenVoice, config: Config) -> str | None:
    """The computer the voice sends its text to, or None if the text stays on this one."""
    engine = chosen.engine
    host = run_plugin(
        engine,
        "saying where its voice sends text",
        lambda: engine.plugin.sends_text_to(config, chosen.voice),
    )
    if host is None:
        return None
    if not isinstance(host, str) or not host.strip() or len(host.split()) != 1:
        raise VoiceError(
            f"The {engine.title} voice engine gave {host!r} as where its voice sends text.",
            hint=f"This is a bug in {engine.package}: it must name a computer, or say None. "
            f"Please report it to {engine.report_to}.",
        )
    return host.strip()


def cloud_voices(voices: Iterable[ChosenVoice], config: Config) -> list[CloudVoice]:
    """Those of `voices` that send text off this computer, with where."""
    found = []
    for chosen in voices:
        host = text_goes_to(chosen, config)
        if host is not None:
            found.append(CloudVoice(chosen, host))
    return found


def refuse_unless_allowed(voices: Iterable[ChosenVoice], config: Config) -> list[CloudVoice]:
    """The cloud voices among `voices` (every voice a run will use). Raises VoiceError if
    there are any and the settings don't allow them, before anything is sent."""
    found = cloud_voices(voices, config)
    if found and not config["voice"]["allow_cloud"]:
        first = found[0]
        raise VoiceError(
            f"The voice {first.chosen.spec} sends the text it speaks to {first.host}, off "
            "this computer. Dogear doesn't send text off this computer unless you allow it, "
            "so nothing was sent.",
            hint=f"To allow it for this run only, add {ALLOW_OPTION}. To allow it every time, "
            "set allow_cloud = true under [voice] in your settings file. To keep the text on "
            "this computer, choose another voice: `dogear voices` says which ones send text.",
        )
    return found


def sends_text(host: str) -> str:
    """How `dogear voices` and the web page mark a cloud voice."""
    return f"sends the book's text to {host}"


def hosts_in_words(found: Iterable[CloudVoice]) -> str:
    """Where the voices send text, each computer once: "a.example", "a.example and
    b.example", or "a.example, b.example, and c.example"."""
    hosts = list(dict.fromkeys(cloud.host for cloud in found))
    if len(hosts) <= 2:
        return " and ".join(hosts)
    return f"{', '.join(hosts[:-1])}, and {hosts[-1]}"


def run_warning(found: list[CloudVoice], main: ChosenVoice) -> list[str]:
    """What a run would send where, for every cloud voice it may use (`found`, from
    voices_for_run), in plain sentences: the web page shows them before a book starts.
    For the main voice alone, the sentences `dogear make` says (`warning`)."""
    if [cloud.chosen.spec for cloud in found] == [main.spec]:
        return warning(found[0])
    others = [cloud for cloud in found if cloud.chosen.spec != main.spec]
    lines = [
        f"The voice {cloud.chosen.voice.name} is spoken by a service at {cloud.host}, not on "
        "this computer."
        + (
            ""
            if cloud.chosen.spec == main.spec
            else " Your settings have it read passages in another language."
        )
        for cloud in found
    ]
    return [
        *lines,
        f"Everything these voices say is sent to {hosts_in_words(found)} as text, one "
        "sentence at a time, and comes back as audio. For a book, that is the text each "
        "voice reads.",
        "Each service's own terms and privacy policy apply to the text, and it may charge for it.",
        "Stopping Dogear stops the sending. What was spoken so far is kept.",
        "To keep text on this computer, choose another voice"
        + (
            ", and take the voices that send it out of [voice.languages] in your settings file."
            if others
            else "."
        ),
    ]


def warning(cloud: CloudVoice) -> list[str]:
    """What will be sent where, in plain sentences, said once before any is sent. The web
    page shows the same sentences before a book starts, when the main voice is the only
    cloud voice a run may use."""
    name, host = cloud.chosen.voice.name, cloud.host
    return [
        f"The voice {name} is spoken by a service at {host}, not on this computer.",
        f"Everything it says is sent to {host} as text, one sentence at a time, and comes "
        "back as audio. For a book, that is the book's text.",
        "That service's own terms and privacy policy apply to the text, and it may charge for it.",
        "Stopping Dogear stops the sending. What was spoken so far is kept.",
        "To keep text on this computer, choose another voice.",
    ]
