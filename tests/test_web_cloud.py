"""Cloud voices on the web page: offered only when the settings allow them, marked with
where they send the book's text, and a book with one starts only after a page that says
so. Stand-in engines; no book is made."""

import lxml.html
import pytest
from engine_fakes import fake_engine, hum_voice
from web_samples import CLOUD_HOST, add_book, sample_app, sample_config, sample_summary, visitor

from dogear.web import choices

CHOSEN = {"format": ["m4b", "daisy"], "voice": "cloud:nova", "speed": "0.9", "section": ["1", "3"]}


@pytest.fixture
def cloud_installed(installed_engines):
    installed_engines(fake_engine("cloud", "Cloud", [hum_voice("nova")], sends_to=CLOUD_HOST))


def site(tmp_path, *, allowed: bool):
    config = sample_config(tmp_path)
    config["voice"]["allow_cloud"] = allowed
    app = sample_app(tmp_path, config)
    book = add_book(app.state.library, tmp_path, state="ready", summary=sample_summary())
    return app, visitor(app), book


def test_cloud_voices_arent_offered_unless_the_settings_allow_them(cloud_installed, config):
    assert [g.label for g in choices.voice_options(config)] == ["Kokoro voices"]
    config["voice"]["allow_cloud"] = True
    groups = choices.voice_options(config)
    assert [g.label for g in groups] == ["Cloud voices", "Kokoro voices"]
    [nova] = groups[0].options
    assert nova.label == f"nova: American English (sends the book's text to {CLOUD_HOST})"
    assert choices.offers_cloud_voices(groups)


def test_a_cloud_voice_sent_anyway_is_turned_down(cloud_installed, tmp_path):
    app, client, book = site(tmp_path, allowed=False)
    page = client.get(f"/books/{book}/choose").text
    assert "cloud:nova" not in page and "sends the book's text" not in page
    response = client.post(f"/books/{book}/choose", data=CHOSEN)
    assert response.status_code == 400
    assert "Choose a voice from the list." in response.text
    assert app.state.library.get(book).state == "ready"


def test_choosing_one_shows_where_the_text_goes_before_anything_starts(cloud_installed, tmp_path):
    app, client, book = site(tmp_path, allowed=True)
    response = client.post(f"/books/{book}/choose", data=CHOSEN)
    assert response.status_code == 200
    page = lxml.html.fromstring(response.text)
    assert page.findtext(".//h1") == f"Send the text of this book to {CLOUD_HOST}?"
    text = page.text_content()
    assert f"The voice nova is spoken by a service at {CLOUD_HOST}, not on this computer." in text
    assert "own terms and privacy policy apply" in text
    assert app.state.library.get(book).state == "ready"  # nothing started
    # The page sends the same choices back, with the answer.
    [form] = page.forms
    sent = [(i.get("name"), i.get("value")) for i in form.xpath(".//input[@type='hidden']")]
    assert sent == [
        ("format", "m4b"),
        ("format", "daisy"),
        ("voice", "cloud:nova"),
        ("speed", "0.9"),
        ("section", "1"),
        ("section", "3"),
    ]
    buttons = [(b.get("name"), b.get("value"), b.text) for b in form.xpath(".//button")]
    assert buttons == [
        ("send", "yes", "Send the text and start making"),
        ("send", "no", "Choose another voice"),
    ]


def test_sending_it_starts_the_book(cloud_installed, tmp_path):
    app, client, book = site(tmp_path, allowed=True)
    response = client.post(
        f"/books/{book}/choose", data={**CHOSEN, "send": "yes"}, follow_redirects=False
    )
    assert response.status_code == 303
    record = app.state.library.get(book)
    assert record.state == "waiting"
    assert record.choices.voice == "cloud:nova" and record.choices.sections == (1, 3)


def test_choosing_another_voice_goes_back_to_the_form_as_it_was(cloud_installed, tmp_path):
    app, client, book = site(tmp_path, allowed=True)
    response = client.post(f"/books/{book}/choose", data={**CHOSEN, "send": "no"})
    assert response.status_code == 200
    page = lxml.html.fromstring(response.text)
    assert page.findtext(".//h1") == "Choose what to make"
    assert page.xpath("//option[@selected]/@value") == ["cloud:nova"]
    assert page.xpath("//input[@name='format'][@checked]/@value") == ["m4b", "daisy"]
    assert "Dogear asks before sending any." in page.text_content()
    assert app.state.library.get(book).state == "ready"


def test_a_voice_on_this_computer_starts_at_once(cloud_installed, tmp_path):
    app, client, book = site(tmp_path, allowed=True)
    response = client.post(
        f"/books/{book}/choose", data={**CHOSEN, "voice": "kokoro:af_heart"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert app.state.library.get(book).state == "waiting"


FAR_HOST = "tts.example.org"


@pytest.fixture
def language_clouds(installed_engines):
    """A French voice and a German voice, each spoken by a service on another computer."""
    installed_engines(
        fake_engine(
            "cloud",
            "Cloud",
            [hum_voice("nova"), hum_voice("amelie", "fr-FR", description="French")],
            sends_to=CLOUD_HOST,
        ),
        fake_engine(
            "far", "Far", [hum_voice("hanna", "de-DE", description="German")], sends_to=FAR_HOST
        ),
    )


def test_a_cloud_voice_for_another_language_is_asked_about_too(language_clouds, tmp_path):
    """Before, only the chosen voice was asked about: a book read by Kokoro, with a cloud
    voice for French in the settings, started at once and sent its French passages."""
    app, client, book = site(tmp_path, allowed=True)
    app.state.config["voice"]["languages"] = {"fr": "cloud:amelie"}
    response = client.post(f"/books/{book}/choose", data={**CHOSEN, "voice": "kokoro:af_heart"})
    assert response.status_code == 200
    page = lxml.html.fromstring(response.text)
    assert page.findtext(".//h1") == f"Send the text of this book to {CLOUD_HOST}?"
    text = page.text_content()
    assert (
        f"The voice amelie is spoken by a service at {CLOUD_HOST}, not on this computer. Your "
        "settings have it read passages in another language."
    ) in text
    assert "take the voices that send it out of [voice.languages]" in text
    assert "Listen" not in text  # Listen says words with Kokoro, on this computer
    assert app.state.library.get(book).state == "ready"
    sent = client.post(
        f"/books/{book}/choose",
        data={**CHOSEN, "voice": "kokoro:af_heart", "send": "yes"},
        follow_redirects=False,
    )
    assert sent.status_code == 303 and app.state.library.get(book).state == "waiting"


def test_every_computer_the_text_may_go_to_is_named(language_clouds, tmp_path):
    app, client, book = site(tmp_path, allowed=True)
    app.state.config["voice"]["languages"] = {"fr": "cloud:amelie", "de": "far:hanna"}
    page = lxml.html.fromstring(client.post(f"/books/{book}/choose", data=CHOSEN).text)
    assert page.findtext(".//h1") == f"Send the text of this book to {CLOUD_HOST} and {FAR_HOST}?"
    said = [p.text_content() for p in page.xpath("//main//p")]
    assert said[1:4] == [
        f"The voice nova is spoken by a service at {CLOUD_HOST}, not on this computer.",
        f"The voice amelie is spoken by a service at {CLOUD_HOST}, not on this computer. Your "
        "settings have it read passages in another language.",
        f"The voice hanna is spoken by a service at {FAR_HOST}, not on this computer. Your "
        "settings have it read passages in another language.",
    ]
    assert f"sent to {CLOUD_HOST} and {FAR_HOST} as text" in said[4]
    assert "uses the voice you chose too" in page.text_content()  # nova, for Listen


def test_the_footer_says_when_book_text_can_leave_this_computer(cloud_installed, tmp_path):
    footer = "//footer//text()"
    _, client, _ = site(tmp_path / "off", allowed=False)
    said = "".join(lxml.html.fromstring(client.get("/").text).xpath(footer))
    assert "stay on that computer." in said
    _, client, _ = site(tmp_path / "on", allowed=True)
    said = "".join(lxml.html.fromstring(client.get("/").text).xpath(footer))
    assert "except the text of a book made with a voice marked" in said
