"""Asking the service for a sentence: what is sent, trying again, the rate limit, and every
error in words. Against the fake service on this computer; waits are recorded, not slept."""

import json

import pytest
from dogear.errors import VoiceError
from fake_service import Reply, tone

from dogear_openai_speech.client import MAX_WAIT_SECONDS, ClientBug, SpeechClient
from dogear_openai_speech.settings import read_settings

ENOUGH = 10_000_000


class Clock:
    """Stands in for time: sleeping moves it on, and each sleep is recorded."""

    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def lines():
    return []


def client(config, clock, lines, key=""):
    return SpeechClient(read_settings(config), key, lines.append, sleep=clock.sleep, clock=clock)


def error_reply(status, message="", **headers):
    body = json.dumps({"error": {"message": message, "code": headers.pop("code", None)}})
    return Reply(status, body.encode(), {"Content-Type": "application/json", **headers})


def test_one_sentence_is_one_request_in_openais_shape(service, config, clock, lines):
    config["openai_speech"]["instructions"] = "Read calmly."
    answer = client(config, clock, lines).speak("marin", "Hello there.", 0.9, ENOUGH)
    assert answer.content_type == "audio/wav" and answer.audio.startswith(b"RIFF")
    [sent] = service.sent
    assert sent.target == "/v1/audio/speech"
    assert sent.body == {
        "model": "gpt-4o-mini-tts",
        "voice": "marin",
        "input": "Hello there.",
        "response_format": "wav",
        "speed": 0.9,
        "instructions": "Read calmly.",
    }
    assert sent.headers["content-type"] == "application/json"
    assert "authorization" not in sent.headers
    assert lines == []


def test_instructions_are_left_out_when_there_are_none(service, config, clock, lines):
    client(config, clock, lines).speak("marin", "Hello.", 1.0, ENOUGH)
    assert "instructions" not in service.sent[0].body


def test_the_key_goes_in_the_authorization_header(service, config, clock, lines):
    client(config, clock, lines, key="sk-test").speak("marin", "Hello.", 1.0, ENOUGH)
    assert service.sent[0].headers["authorization"] == "Bearer sk-test"


def test_asked_to_slow_down_it_waits_as_long_as_it_is_told(service, config, clock, lines):
    service.replies = [error_reply(429, "Rate limit reached.", **{"Retry-After": "7"})]
    answer = client(config, clock, lines).speak("marin", "Hello.", 1.0, ENOUGH)
    assert answer.audio.startswith(b"RIFF") and len(service.sent) == 2
    assert 7 in clock.slept
    assert lines == [
        f"{service.url.split('//')[1].split('/')[0]} didn't speak a sentence: it asked Dogear "
        "to slow down. Trying again in 7 seconds, which is try 2 of 5."
    ]


def test_server_errors_are_tried_again_waiting_longer_each_time(service, config, clock, lines):
    config["openai_speech"]["requests_per_minute"] = 0
    service.replies = [Reply(503), Reply(500), Reply(502)]
    client(config, clock, lines).speak("marin", "Hello.", 1.0, ENOUGH)
    assert clock.slept == [2.0, 4.0, 8.0]


def test_a_wait_is_never_longer_than_two_minutes(service, config, clock, lines):
    config["openai_speech"]["requests_per_minute"] = 0
    service.replies = [error_reply(429, **{"Retry-After": "86400"})]
    client(config, clock, lines).speak("marin", "Hello.", 1.0, ENOUGH)
    assert clock.slept == [MAX_WAIT_SECONDS]


def test_still_failing_after_every_try_says_to_try_later(service, config, clock, lines):
    config["openai_speech"]["retries"] = 2
    service.replies = [Reply(503)] * 3
    with pytest.raises(VoiceError, match="had a problem of its own \\(503") as error:
        client(config, clock, lines).speak("marin", "Hello.", 1.0, ENOUGH)
    assert "after 3 tries" in error.value.message
    assert error.value.hint.startswith("Try again later")
    assert "sentences spoken so far are kept" in error.value.hint


def test_still_too_many_requests_says_to_lower_the_rate(service, config, clock, lines):
    config["openai_speech"]["retries"] = 0
    service.replies = [error_reply(429, "Rate limit reached for requests.")]
    with pytest.raises(VoiceError, match="asked Dogear to slow down") as error:
        client(config, clock, lines).speak("marin", "Hello.", 1.0, ENOUGH)
    assert "Rate limit reached for requests." in error.value.message
    assert "requests_per_minute" in error.value.hint


def test_no_credit_left_isnt_tried_again(service, config, clock, lines):
    service.replies = [error_reply(429, "You exceeded your quota.", code="insufficient_quota")]
    with pytest.raises(VoiceError, match="run out of credit"):
        client(config, clock, lines).speak("marin", "Hello.", 1.0, ENOUGH)
    assert len(service.sent) == 1


def test_a_turned_down_key_names_the_variable_and_never_repeats_the_key(
    service, config, clock, lines
):
    service.replies = [error_reply(401, "Incorrect API key provided: sk-sec***cret.")]
    with pytest.raises(VoiceError, match="turned down the key \\(401") as error:
        client(config, clock, lines, key="sk-secret").speak("marin", "Hello.", 1.0, ENOUGH)
    said = error.value.message + error.value.hint
    assert "sk-sec" not in said and "OPENAI_API_KEY" in error.value.hint
    assert len(service.sent) == 1


SECRET_PARTS = ("FIRSTPART", "SECONDPART", "sk-proj")


def shows_no_key(*texts):
    return not any(part in text for part in SECRET_PARTS for text in texts)


@pytest.mark.parametrize(
    "key",
    ["sk-proj-FIRSTPART\nsk-proj-SECONDPART", "sk-proj-FIRSTPART’SECONDPART"],
)
def test_a_key_python_wont_send_is_an_error_that_doesnt_show_it(service, config, clock, lines, key):
    # settings.api_key refuses these keys; this is what happens if one gets past it.
    with pytest.raises(VoiceError, match="a header in it has a character") as error:
        client(config, clock, lines, key=key).speak("marin", "Hello.", 1.0, ENOUGH)
    assert shows_no_key(error.value.message, error.value.hint, str(error.value), *lines)
    assert error.value.__cause__ is None and error.value.__suppress_context__
    assert "OPENAI_API_KEY" in error.value.hint
    assert service.sent == []


def test_the_key_is_taken_out_of_any_error_that_leaves_the_client(
    service, config, clock, lines, monkeypatch
):
    key = "sk-proj-FIRSTPART\nsk-proj-SECONDPART"

    def fails(self, data, most_bytes):
        try:
            raise OSError(f"inner {key!r}")
        except OSError:
            raise RuntimeError(f"Header {key.encode()!r} or {key} or {key!r}") from None

    monkeypatch.setattr(SpeechClient, "_send", fails)
    with pytest.raises(ClientBug) as error:
        client(config, clock, lines, key=key).speak("marin", "Hello.", 1.0, ENOUGH)
    assert str(error.value).startswith("RuntimeError: Header b'(your key)' or")
    assert shows_no_key(str(error.value))
    assert error.value.__suppress_context__  # the errors it came from aren't shown

    def says_it(self, data, most_bytes):
        raise VoiceError(f"It said {key}.", hint=f"Not {key!r}.")

    monkeypatch.setattr(SpeechClient, "_send", says_it)
    with pytest.raises(VoiceError) as error:
        client(config, clock, lines, key=key).speak("marin", "Hello.", 1.0, ENOUGH)
    assert error.value.message == "It said (your key)." and error.value.hint == "Not '(your key)'."
    assert shows_no_key(str(error.value))


def test_an_error_without_the_key_in_it_is_left_as_it_was(config, clock, lines, monkeypatch):
    def fails(self, data, most_bytes):
        raise KeyError("no key here")

    monkeypatch.setattr(SpeechClient, "_send", fails)
    with pytest.raises(KeyError, match="no key here"):
        client(config, clock, lines, key="sk-proj-FIRSTPART").speak("marin", "Hi.", 1.0, ENOUGH)


def test_a_service_that_repeats_the_key_in_its_reason_never_shows_it(service, config, clock, lines):
    key = "sk-proj-FIRSTPARTSECONDPART"
    service.replies = [error_reply(400, f"Bad key {key} in\n the request.")]
    with pytest.raises(VoiceError) as error:
        client(config, clock, lines, key=key).speak("marin", "Hello.", 1.0, ENOUGH)
    assert "Bad key (your key) in the request." in error.value.message
    assert shows_no_key(error.value.message, error.value.hint)


@pytest.mark.parametrize(
    ("reply", "message", "hint"),
    [
        (error_reply(400, "Invalid voice 'x'."), "turned down the request (400", "voices"),
        (error_reply(404, "No such model."), "has no speech service at", "base_url and model"),
        (error_reply(403, "Not allowed."), "refused the request (403", "model gpt-4o-mini-tts"),
        (error_reply(413), "says a sentence is too long", "max_input_chars"),
        (Reply(302, b"", {"Location": "https://elsewhere.example/v1"}), "somewhere else", ""),
    ],
)
def test_other_answers_say_what_to_check(service, config, clock, lines, reply, message, hint):
    service.replies = [reply]
    with pytest.raises(VoiceError) as error:
        client(config, clock, lines).speak("marin", "Hello.", 1.0, ENOUGH)
    assert message in error.value.message
    assert hint in error.value.hint
    assert len(service.sent) == 1  # not tried again, and no redirect followed


def test_the_services_reason_is_kept_short_and_on_one_line(service, config, clock, lines):
    service.replies = [error_reply(400, "Bad\n\nvoice. " + "x" * 1000)]
    with pytest.raises(VoiceError) as error:
        client(config, clock, lines).speak("marin", "Hello.", 1.0, ENOUGH)
    assert "Bad voice." in error.value.message and "\n" not in error.value.message
    assert len(error.value.message) < 400


def test_a_200_that_isnt_audio_is_an_error(service, config, clock, lines):
    service.replies = [error_reply(200, "Model loading.")]
    with pytest.raises(VoiceError, match="sent back application/json, not audio: Model loading"):
        client(config, clock, lines).speak("marin", "Hello.", 1.0, ENOUGH)


def test_too_much_audio_is_not_read(service, config, clock, lines):
    with pytest.raises(VoiceError, match="more than a sentence's audio could be"):
        client(config, clock, lines).speak("marin", "Hello.", 1.0, 100)


def test_a_server_that_isnt_running_says_to_start_it(config, clock, lines):
    config["openai_speech"]["base_url"] = "http://127.0.0.1:9/v1"  # nothing listens there
    config["openai_speech"]["retries"] = 1
    with pytest.raises(VoiceError, match="couldn't reach 127.0.0.1:9") as error:
        client(config, clock, lines).speak("marin", "Hello.", 1.0, ENOUGH)
    assert "Check that the server is running at http://127.0.0.1:9/v1" in error.value.hint
    assert "the connection failed" in lines[0]


def test_requests_wait_their_turn(service, config, clock, lines):
    config["openai_speech"]["requests_per_minute"] = 30
    speaking = client(config, clock, lines)
    for _ in range(3):
        speaking.speak("marin", "Hello.", 1.0, ENOUGH)
    assert clock.slept == [2.0, 2.0]


def test_a_server_on_this_computer_is_reached_without_a_proxy(
    service, config, clock, lines, monkeypatch
):
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")  # would fail if it were used
    client(config, clock, lines).speak("marin", "Hello.", 1.0, ENOUGH)
    assert len(service.sent) == 1


def test_another_computer_is_reached_through_the_environments_proxy(
    service, config, clock, lines, far_away
):
    config["openai_speech"]["base_url"] = far_away
    config["openai_speech"]["api_key_env"] = ""
    client(config, clock, lines).speak("marin", "Hello.", 1.0, ENOUGH)
    [sent] = service.sent
    assert sent.host == "speech.invalid"
    assert sent.target == "http://speech.invalid/v1/audio/speech"


def test_the_tone_grows_with_the_sentence():
    assert len(tone(1.0)) > len(tone(0.5))
