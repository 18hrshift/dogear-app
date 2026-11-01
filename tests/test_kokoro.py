"""The Kokoro engine with onnxruntime faked: what it feeds the model, and its cache keys."""

import json
import random

import numpy as np
import pytest
from kokoro_fakes import STYLE_WIDTH, use_fake_session, write_tokenizer, write_voice

from dogear.errors import PronunciationError, VoiceError
from dogear.pronunciations import load_dictionary
from dogear.voice.kokoro import KokoroEngine, check_phonemes, load_vocab, split_phonemes

# split_phonemes


@pytest.mark.parametrize(
    ("phonemes", "limit", "pieces"),
    [
        ("  ab cd  ", 10, ["ab cd"]),
        ("", 10, []),
        ("   ", 10, []),
        ("abcdefghij", 10, ["abcdefghij"]),  # exactly the limit
        ("aaa, bbb ccc", 10, ["aaa,", "bbb ccc"]),  # punctuation before a later space
        ("aa, bb. cc dd ee", 12, ["aa, bb.", "cc dd ee"]),  # the last punctuation that fits
        ("aaaa bbbb cccc", 10, ["aaaa bbbb", "cccc"]),  # a space when there's no punctuation
        ("abcdefghij", 4, ["abcd", "efgh", "ij"]),  # neither: cut at the limit
        ("aaaa\u2014bbbb cc", 8, ["aaaa\u2014", "bbbb cc"]),
        ("aaa… bbbb", 6, ["aaa…", "bbbb"]),
        ("ab? cd! ef; gh: ij", 7, ["ab? cd!", "ef; gh:", "ij"]),
    ],
)
def test_split_phonemes(phonemes, limit, pieces):
    assert split_phonemes(phonemes, limit) == pieces


def random_phonemes(rng: random.Random) -> str:
    return "".join(rng.choice("həlˈO wɜɹd   ,.!?;:\u2014…") for _ in range(rng.randint(0, 120)))


def test_split_phonemes_properties():
    rng = random.Random(1234)
    for _ in range(500):
        phonemes = random_phonemes(rng)
        limit = rng.randint(1, 40)
        pieces = split_phonemes(phonemes, limit)
        case = f"{phonemes!r} at {limit}: {pieces!r}"
        assert all(pieces), f"empty piece for {case}"
        assert all(len(p) <= limit for p in pieces), f"piece too long for {case}"
        assert all(p == p.strip() for p in pieces), f"untrimmed piece for {case}"
        # Joined back with spaces, only whitespace differs.
        rejoined = "".join(" ".join(pieces).split())
        assert rejoined == "".join(phonemes.split()), f"lost text for {case}"


# load_vocab


def test_load_vocab(tmp_path):
    path = tmp_path / "tokenizer.json"
    data = {"model": {"type": "WordLevel", "vocab": {"$": 0, ";": 1, "a": 43, "ˈ": 156}}}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert load_vocab(path) == {"$": 0, ";": 1, "a": 43, "ˈ": 156}


# KokoroEngine

SYMBOLS = "abcdə ˈ.,"
ROWS = 20


@pytest.fixture
def session(monkeypatch):
    return use_fake_session(monkeypatch)


@pytest.fixture
def make(tmp_path, session):
    """Builds a KokoroEngine on stand-in files; keyword arguments override the defaults."""
    tokenizer = tmp_path / "tokenizer.json"
    vocab = write_tokenizer(tokenizer, SYMBOLS)
    voice = tmp_path / "voice.bin"
    write_voice(voice, ROWS)

    def build(**overrides):
        settings = {
            "model": tmp_path / "model.onnx",
            "tokenizer": tokenizer,
            "voice": voice,
            "g2p": lambda text: text,
            "speed": 1.0,
            "threads": 2,
            "sample_rate": 24000,
            "max_phonemes": ROWS,
            "identity": "kokoro|model|voice",
            **overrides,
        }
        return KokoroEngine(**settings)

    build.vocab = vocab
    return build


def test_engine_setup(make, session, tmp_path):
    engine = make(threads=3, sample_rate=22050)
    assert engine.sample_rate == 22050
    [s] = session.instances
    assert s.model == str(tmp_path / "model.onnx")
    assert s.options.intra_op_num_threads == 3
    assert s.providers == ["CPUExecutionProvider"]


def test_input_ids_are_padded_at_both_ends(make, session):
    engine = make()
    engine.render(engine.plan("abc"))
    [feeds] = session.instances[0].feeds
    v = make.vocab
    assert feeds["input_ids"].dtype == np.int64
    assert feeds["input_ids"].tolist() == [[0, v["a"], v["b"], v["c"], 0]]


@pytest.mark.parametrize("text", ["a", "abc", "abcd əb", "a" * ROWS])
def test_style_row_is_chosen_by_length(make, session, text):
    engine = make()
    engine.render(engine.plan(text))
    [feeds] = session.instances[0].feeds
    assert feeds["style"].shape == (1, STYLE_WIDTH)
    assert np.all(feeds["style"] == len(text) - 1)


def test_speed_is_passed_through(make, session):
    engine = make(speed=0.85)
    engine.render(engine.plan("abc"))
    speed = session.instances[0].feeds[0]["speed"]
    assert speed.dtype == np.float32
    assert speed.tolist() == pytest.approx([0.85])


def test_phonemes_missing_from_the_vocabulary_are_dropped(make, session):
    engine = make(g2p=lambda text: "aXb❓c")
    plan = engine.plan("anything")
    assert plan.payload == "abc"
    engine.render(plan)
    assert len(session.instances[0].feeds[0]["input_ids"][0]) == 5


def test_plan_uses_the_g2p(make):
    seen = []

    def g2p(text):
        seen.append(text)
        return "ˈab"

    assert make(g2p=g2p).plan("Hello there.").payload == "ˈab"
    assert seen == ["Hello there."]


def test_plan_key_is_stable(make):
    first = make().plan("abc")
    assert make().plan("abc") == first
    assert len(first.key) == 64


@pytest.mark.parametrize(
    "change",
    [
        {"identity": "kokoro|model|other-voice"},
        {"identity": "kokoro|other-model|voice"},
        {"speed": 1.1},
    ],
)
def test_plan_key_changes_with_the_voice_and_speed(make, change):
    assert make(**change).plan("abc").key != make().plan("abc").key


def test_plan_key_changes_with_the_phonemes(make):
    engine = make()
    assert engine.plan("abc").key != engine.plan("abd").key


def test_plan_key_covers_the_phonemes_not_the_spelling(make):
    engine = make(g2p=str.lower)
    assert engine.plan("ABC").key == engine.plan("abc").key


def test_plan_key_is_unaffected_by_unknown_symbols(make):
    # Dropped symbols make no sound, so the audio and the key are the same.
    assert make(g2p=lambda t: "aXbc").plan("").key == make(g2p=lambda t: "abc").plan("").key


def test_long_input_is_spoken_in_pieces(make, session):
    engine = make(max_phonemes=8)
    plan = engine.plan("abc abcd, ab ba ca cb")
    pieces = split_phonemes(plan.payload, 8)
    assert len(pieces) > 1
    audio = engine.render(plan)
    feeds = session.instances[0].feeds
    assert len(feeds) == len(pieces)
    v = make.vocab
    for piece, feed in zip(pieces, feeds, strict=True):
        assert feed["input_ids"].tolist() == [[0, *(v[c] for c in piece), 0]]
        assert np.all(feed["style"] == len(piece) - 1)
    # The pieces are joined in order: the fake marks each call's samples with its number.
    lengths = [10 * (len(p) + 2) for p in pieces]
    assert audio.dtype == np.float32
    assert audio.ndim == 1
    assert len(audio) == sum(lengths)
    start = 0
    for n, length in enumerate(lengths, start=1):
        assert np.allclose(audio[start : start + length], n / 100)
        start += length


def test_nothing_to_say_is_empty_audio(make, session):
    engine = make(g2p=lambda text: "  ")
    audio = engine.render(engine.plan("..."))
    assert audio.dtype == np.float32
    assert len(audio) == 0
    assert session.instances[0].feeds == []


# check_phonemes: symbols in the user's phoneme entries.

VOCAB = {c: i for i, c in enumerate(" abcdelnjæəˈˌɡ", start=1)}


def dictionary(tmp_path, text):
    path = tmp_path / "words.toml"
    path.write_text("[words]\n" + text, encoding="utf-8")
    return load_dictionary([path]), path


def test_symbols_the_voice_has_pass(tmp_path):
    words, _ = dictionary(tmp_path, 'Lanyon = { phonemes = "lˈænjən" }\nX = "anything ✓"\n')
    check_phonemes(words, VOCAB)


def test_a_symbol_the_voice_lacks_names_the_symbol_and_the_entry(tmp_path):
    words, path = dictionary(
        tmp_path, 'Lanyon = { phonemes = "lˈænjən" }\nGale = { phonemes = "gˈAl" }\n'
    )
    with pytest.raises(PronunciationError) as err:
        check_phonemes(words, VOCAB)
    assert err.value.message == (
        f"The phonemes for 'Gale' in {path} use 'g' (latin small letter g) and 'A' (latin "
        "capital letter a), which the voice has no sound for."
    )
    assert "ɡ, the script g (U+0261)" in err.value.hint
    assert "EN_PHONES.md" in err.value.hint
    assert "--phonemes" in err.value.hint


def test_an_apostrophe_for_stress_gets_a_tip(tmp_path):
    words, _ = dictionary(tmp_path, 'Lanyon = { phonemes = "l\'ænjən" }\n')
    with pytest.raises(PronunciationError, match="'\\'' \\(apostrophe\\)") as err:
        check_phonemes(words, VOCAB)
    assert "Stress is written ˈ (U+02C8)" in err.value.hint


# Damaged voice files


def test_a_model_onnxruntime_cant_load_says_to_check_the_voice_files(make, monkeypatch, tmp_path):
    monkeypatch.undo()  # the real onnxruntime, given a file that isn't a model
    model = tmp_path / "model.onnx"
    model.write_bytes(b"not a model")
    with pytest.raises(VoiceError) as error:
        make(model=model)
    assert error.value.message == (
        f"The voice can't use {model}: onnxruntime couldn't load it. The file may be damaged."
    )
    assert "dogear download --check" in error.value.hint


def test_a_voice_file_of_the_wrong_size_says_to_check_the_voice_files(make, tmp_path):
    voice = tmp_path / "short.bin"
    voice.write_bytes(b"\0" * (4 * STYLE_WIDTH + 2))
    with pytest.raises(VoiceError, match="isn't the size a voice file is") as error:
        make(voice=voice)
    assert "dogear download --check" in error.value.hint


@pytest.mark.parametrize("text", ["{not json", '{"model": {}}', "[]", "\udcff"])
def test_a_tokenizer_that_cant_be_read_says_to_check_the_voice_files(tmp_path, text):
    path = tmp_path / "tokenizer.json"
    path.write_text(text, encoding="utf-8", errors="surrogateescape")
    with pytest.raises(VoiceError, match="isn't the tokenizer Kokoro has") as error:
        load_vocab(path)
    assert "dogear download --check" in error.value.hint
