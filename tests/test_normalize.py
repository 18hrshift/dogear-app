import copy

import pytest

from dogear.speech.normalize import SpeechRules, normalize_for_speech, normalize_heading
from dogear.speech.roman import roman_value
from dogear.speech.sentences import SentenceRules, split_sentences


@pytest.fixture
def say(config):
    rules = SpeechRules.from_config(config)
    return lambda text, lang="en": normalize_for_speech(text, rules, lang)


@pytest.fixture
def heading(config):
    rules = SpeechRules.from_config(config)
    return lambda text, lang="en": normalize_heading(text, rules, lang)


def say_with(config, change):
    """Normalize with a copy of the config that `change` edits."""
    custom = copy.deepcopy(config)
    change(custom["speech"])
    rules = SpeechRules.from_config(custom)
    return lambda text: normalize_for_speech(text, rules, "en")


# Ported from audiobook-maker's tests.
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Dr. Smith", "Doctor Smith"),
        ("fruits, vegetables, etc.", "et cetera"),
        ("cats vs. dogs", "versus"),
        ("There were 42 apples.", "forty-two"),
        ("He finished 1st in the race.", "first"),
    ],
)
def test_ported_expansions(say, text, expected):
    assert expected in say(text)


def test_ported_full_pipeline(say):
    result = say("Dr. Johnson published 3 papers in 1999, etc.")
    assert result == "Doctor Johnson published three papers in nineteen ninety-nine, et cetera."


# Bugs in the old preprocessor, fixed here.
def test_no_at_sentence_end_is_not_number(say):
    assert say("He said no.") == "He said no."


def test_no_before_number_is_number(say):
    assert say("See No. 5 on p. 12.") == "See number five on page twelve."


def test_decimal_digits_read_one_by_one(say):
    assert say("Pi is 3.14 and 3.05.") == "Pi is three point one four and three point zero five."


def test_comma_grouped_numbers(say):
    assert say("1,000 people") == "one thousand people"
    assert say("1,500,000") == "one million, five hundred thousand"


def test_title_needs_a_name_after_it(say):
    assert say("Mrs. Jones lives on Elm Dr. now.") == "Missus Jones lives on Elm Dr. now."


def test_longer_abbreviation_wins(say):
    assert say("pp. 3") == "pages three"


# Numbers.
def test_years_spoken_as_years(say):
    assert say("In 1984 and 2023.") == "In nineteen eighty-four and twenty twenty-three."


def test_long_codes_and_leading_zeros_read_digit_by_digit(say):
    assert say("agent 007") == "agent zero zero seven"
    assert say("code 12345678") == "code one two three four five six seven eight"


def test_numbers_inside_words_untouched(say):
    assert say("Version v1.2 of the MP3 file.") == "Version v1.2 of the MP3 file."


def test_number_range_with_en_dash(say):
    assert say("pages 3–4") == "pages three to four"


def test_other_languages_keep_their_numbers_for_their_voice(say):
    # "1,5" is one and a half in French: an English reading would get it wrong.
    assert say("Il a 42 ans et 1,5 kilo.", lang="fr") == "Il a 42 ans et 1,5 kilo."
    assert say("42", lang="xx") == "42"


def test_a_range_in_another_language_is_left_to_its_voice(say):
    assert say("de 1990–1995", lang="fr") == "de 1990-1995"
    assert say("Il dit—doucement.", lang="fr") == "Il dit, doucement."


# Punctuation and leftovers.
def test_curly_quotes_dashes_and_ellipsis(say):
    assert say("“Hi,” she said—quietly…") == '"Hi," she said, quietly...'


def test_invisible_formatting_characters_removed(say):
    # Standard Ebooks puts a word joiner before each em dash.
    assert say("the fire\u2060\u2014a large man") == "the fire, a large man"
    assert say("zero\u200bwidth soft\u00adhyphen") == "zerowidth softhyphen"


def test_markup_characters_removed(say):
    assert say("a *bold* _word_") == "a bold word"


def test_abbreviation_period_kept_only_at_sentence_end(say):
    assert say("apples, etc. and pears") == "apples, et cetera and pears"
    assert say("apples, etc.") == "apples, et cetera."


# Config is the source of truth.
def test_config_table_changes_behavior(config):
    custom = copy.deepcopy(config)
    custom["speech"]["abbreviations"]["approx."] = "about"
    rules = SpeechRules.from_config(custom)
    assert normalize_for_speech("approx. ten", rules, "en") == "about ten"


def test_config_unit_table_changes_behavior(config):
    say = say_with(config, lambda speech: speech["units"].update({"km": ["klick", "klicks"]}))
    assert say("1 km and 5 km") == "one klick and five klicks"


def test_config_currency_tables_change_behavior(config):
    def change(speech):
        speech["currency"]["₿"] = ["bitcoin", "bitcoins"]
        speech["currency_scales"]["lakh"] = "lakh"
        del speech["currency_cents"]["$"]

    say = say_with(config, change)
    assert say("₿1 or ₿2") == "one bitcoin or two bitcoins"
    assert say("₹5 lakh") == "five lakh rupees"
    assert say("$5.99") == "five point nine nine dollars"


def test_config_time_suffixes_change_behavior(config):
    say = say_with(config, lambda speech: speech["time_suffixes"].update(pm="in the afternoon"))
    assert say("at 3:30 pm") == "at three thirty in the afternoon"


def test_config_roman_keywords_change_behavior(config):
    say = say_with(config, lambda speech: speech["roman_numeral_keywords"].append("Vol."))
    # The numeral becomes digits first, so the "Vol." abbreviation then expands.
    assert say("Vol. IV") == "volume four"
    say = say_with(config, lambda speech: speech.update(roman_numeral_keywords=[]))
    assert say("Chapter IV") == "Chapter IV"


def test_empty_tables_turn_passes_off(config):
    def change(speech):
        for table in ("currency", "units", "time_suffixes", "currency_scales"):
            speech[table] = {}

    say = say_with(config, change)
    assert say("$5 and 5 km at 10 am") == "$five and five km at ten am"
    assert say("at 3:30") == "at three thirty"  # a clock time needs no suffix table


# Currency.
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("$5", "five dollars"),
        ("$1", "one dollar"),
        ("$5.99", "five dollars and ninety-nine cents"),
        ("$1.01", "one dollar and one cent"),
        ("$0.50", "fifty cents"),
        ("$0.01", "one cent"),
        ("$5.00", "five dollars"),
        ("$0.00", "zero dollars"),
        ("$1,000", "one thousand dollars"),
        ("$1,000.50", "one thousand dollars and fifty cents"),
        ("£3", "three pounds"),
        ("£1", "one pound"),
        ("£1.50", "one pound and fifty pence"),
        ("€10", "ten euros"),
        ("¥500", "five hundred yen"),
        ("5¢", "five cents"),
        ("1¢", "one cent"),
    ],
)
def test_currency(say, text, expected):
    assert say(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("$1.5 million", "one point five million dollars"),
        ("$1 million", "one million dollars"),
        ("$2 Billion", "two billion dollars"),
        ("£5m", "five million pounds"),
        ("$2B", "two billion dollars"),
        ("€3bn", "three billion euros"),
        ("$50k", "fifty thousand dollars"),
    ],
)
def test_currency_with_scale_words(say, text, expected):
    assert say(text) == expected


def test_currency_amounts_are_cardinals_not_years_or_codes(say):
    assert say("$2023") == "two thousand and twenty-three dollars"
    assert say("$1999") == "one thousand, nine hundred and ninety-nine dollars"


def test_currency_without_two_decimal_places_reads_a_decimal(say):
    assert say("$5.5") == "five point five dollars"
    assert say("¥5.99") == "five point nine nine yen"  # yen has no cents entry


def test_currency_in_a_sentence(say):
    assert say("It cost $5.") == "It cost five dollars."
    assert say("Prices rose from $5 to $7.25.") == (
        "Prices rose from five dollars to seven dollars and twenty-five cents."
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("He paid in $ and £.", "He paid in $ and £."),  # a symbol with no amount
        ("$5 millionaires", "five dollars millionaires"),  # "million" must end the word
        ("$5 more", "five dollars more"),  # "m" for million must end the word
    ],
)
def test_currency_leaves_lookalikes(say, text, expected):
    assert say(text) == expected


# Times.
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("3:30", "three thirty"),
        ("3:05", "three oh five"),
        ("10:00", "ten o'clock"),
        ("12:45", "twelve forty-five"),
        ("14:00", "fourteen hundred"),
        ("14:30", "fourteen thirty"),
        ("3:30 pm", "three thirty p.m."),
        ("3:30 p.m.", "three thirty p.m."),
        ("3:30PM", "three thirty p.m."),
        ("10:00 a.m.", "ten a.m."),
        ("10 a.m.", "ten a.m."),
        ("10am", "ten a.m."),
        ("7 PM", "seven p.m."),
    ],
)
def test_times(say, text, expected):
    assert say(text) == expected


def test_times_in_sentences(say):
    assert say("We met at 3:30 p.m. on Monday.") == "We met at three thirty p.m. on Monday."
    assert say("We met at 3 pm.") == "We met at three p.m."  # one period, not two
    assert say("The train left at 10:00.") == "The train left at ten o'clock."
    assert say("from 9:30–11:00 am") == "from nine thirty to eleven a.m."


def test_bible_verse_reads_like_a_time(say):
    assert say("John 3:16") == "John three sixteen"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("5 amazing days", "five amazing days"),  # "am" must end the word
        ("the 5 PMs", "the five PMs"),
        ("a 3:1 ratio", "a three:one ratio"),  # one-digit "minutes" are not a time
        ("12:30:45", "twelve:thirty:forty-five"),  # seconds are not read as a clock
        ("25:30", "twenty-five:thirty"),  # no such hour
    ],
)
def test_times_leave_lookalikes(say, text, expected):
    assert say(text) == expected


# Units.
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("5 km", "five kilometers"),
        ("1 km", "one kilometer"),
        ("1.5 km", "one point five kilometers"),
        ("0 km", "zero kilometers"),
        ("5km", "five kilometers"),
        ("1,000 km", "one thousand kilometers"),
        ("1984 km", "one thousand, nine hundred and eighty-four kilometers"),
        ("6 ft", "six feet"),
        ("1 ft", "one foot"),
        ("3 yd", "three yards"),
        ("26.2 mi", "twenty-six point two miles"),
        ("5 mm", "five millimeters"),
        ("500 g", "five hundred grams"),
        ("1 kg", "one kilogram"),
        ("8 oz", "eight ounces"),
        ("1 lb", "one pound"),
        ("250 ml", "two hundred and fifty milliliters"),
        ("2 fl oz", "two fluid ounces"),
        ("1 tbsp", "one tablespoon"),
        ("60 mph", "sixty miles per hour"),
        ("100 km/h", "one hundred kilometers per hour"),
        ("20°C", "twenty degrees Celsius"),
        ("20 °C", "twenty degrees Celsius"),
        ("1°C", "one degree Celsius"),
        ("98.6°F", "ninety-eight point six degrees Fahrenheit"),
        ("90°", "ninety degrees"),
        ("50%", "fifty percent"),
        ("1%", "one percent"),
        ("0.5%", "zero point five percent"),
        ("5–10 km", "five to ten kilometers"),
    ],
)
def test_units(say, text, expected):
    assert say(text) == expected


def test_dotted_units_keep_only_a_sentence_ending_period(say):
    assert say("He weighed 200 lbs.") == "He weighed two hundred pounds."
    assert say("Add 2 lbs. of flour.") == "Add two pounds of flour."
    assert say("He stood 6 ft. tall") == "He stood six feet tall"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("5 in the morning", "five in the morning"),  # "in" is never a unit
        ("ran 5 m", "ran five m"),  # meters or minutes: left alone
        ("took 5 s", "took five s"),
        ("5 gals", "five gals"),  # a unit must end the word
        ("5 kmph", "five kmph"),
        ("a km away", "a km away"),  # no number, no unit
        ("v1.5 km", "v1.5 km"),  # a number inside a code stays
        ("5 G network", "five G network"),  # units are case-sensitive
    ],
)
def test_units_leave_lookalikes(say, text, expected):
    assert say(text) == expected


# Roman numerals.
@pytest.mark.parametrize(
    ("token", "value"),
    [
        ("I", 1),
        ("IV", 4),
        ("IX", 9),
        ("XIV", 14),
        ("XL", 40),
        ("XCIX", 99),
        ("CCCXCIX", 399),
        ("ii", 2),
        ("xiv", 14),
        ("IIII", None),
        ("VX", None),
        ("IC", None),
        ("L", None),  # lone L and C are labels, not numbers
        ("C", None),
        ("MIX", None),  # D and M are never read
        ("DC", None),
        ("Iv", None),
    ],
)
def test_roman_value(token, value):
    assert roman_value(token) == value


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Chapter IV", "Chapter four"),
        ("CHAPTER XII", "CHAPTER twelve"),
        ("in Chapter IV we saw", "in Chapter four we saw"),
        ("Act III, Scene ii", "Act three, Scene two"),
        ("See Book I.", "See Book one."),
        ("Part I, Chapter 3", "Part one, Chapter three"),
        ("Psalm XXIII", "Psalm twenty-three"),
        ("Letter IV", "Letter four"),
        ("Appendix II", "Appendix two"),
        ("Canto XXXIV", "Canto thirty-four"),
    ],
)
def test_roman_numerals_after_keywords(say, text, expected):
    assert say(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "I am here, and I said I would be.",
        "MIX it, DID it, CIVIL war, LIVID.",
        "World War II ended.",
        "Henry VIII and Louis XIV.",
        "Pope John Paul II",
        "the part I played",
        "the Part I played",  # a lone I followed by a word is the pronoun
        "the Book I'm reading",
        "the letter X",  # keywords match only as written or in capitals
        "in chapter IV",
        "Appendix C",  # a lone C or L is a label
        "Chapter L",
        "Chapter DIV",  # D and M are never read
        "Chapter IIII",  # not a well-formed numeral
        "Chapter Iv",
        "Subchapter IV",  # the keyword must be a whole word
    ],
)
def test_roman_numerals_left_alone(say, text):
    assert say(text) == text


def test_lone_i_before_a_word_stays_even_beside_a_converted_numeral(say):
    # Known limit: the pronoun rule wins, so a lone "I" before a word is left as written.
    assert say("Book I and Book II") == "Book I and Book two"


# Headings.
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("IV", "four"),
        ("IV.", "four."),
        ("  XII  ", "twelve"),
        ("I", "one"),
        ("I.", "one."),
        ("X", "ten"),
        ("iv", "four"),
        ("IV. The Return", "four. The Return"),
        ("I. Loomings", "one. Loomings"),
        ("XIV: Home", "fourteen: Home"),
        ("Chapter I", "Chapter one"),
        ("CHAPTER I THE BEGINNING", "CHAPTER one THE BEGINNING"),
        ("Book I Read", "Book one Read"),  # at a heading's start, the keyword wins
        ("Chapter IV: The $5 Question", "Chapter four: The five dollars Question"),
        ("Stave I: Marley's Ghost", "Stave one: Marley's Ghost"),
    ],
)
def test_heading_numerals(heading, text, expected):
    assert heading(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "I Know Why the Caged Bird Sings",
        "I, Claudius",
        "I. M. Pei",  # initials, not a number
        "V. S. Naipaul",
        "V for Vendetta",
        "World War II",
        "The Part I Played",
        "MIX",
        "DC",
        "The Letter",
    ],
)
def test_heading_numerals_left_alone(heading, text):
    assert heading(text) == text


def test_body_text_does_not_read_a_bare_numeral(say):
    assert say("IV.") == "IV."
    assert say("I.") == "I."


# Languages.
def test_every_rewrite_is_english_only(say, heading):
    assert say("$5", lang="fr") == "$5"
    assert say("5 km", lang="de") == "5 km"
    assert say("3:30 pm", lang="fr") == "3:30 pm"
    assert say("Chapitre IV", lang="fr") == "Chapitre IV"
    assert heading("IV", lang="fr") == "IV"
    assert say("Dr. Martin, etc.", lang="fr") == "Dr. Martin, etc."


def test_pronunciation_entries_apply_to_their_own_languages(config, tmp_path):
    words = tmp_path / "words.toml"
    words.write_text(
        '[words]\nUtterson = "utter son"\nMme = { sounds_like = "madame", languages = ["fr"] }\n',
        encoding="utf-8",
    )
    config["pronunciation"]["files"] = [str(words)]
    rules = SpeechRules.from_config(config)
    assert normalize_for_speech("Mme Utterson.", rules, "en") == "Mme Utter son."
    assert normalize_for_speech("Mme Utterson.", rules, "fr-FR") == "Madame Utterson."
    assert normalize_for_speech("Mme Utterson.", rules, "de") == "Mme Utterson."


def test_english_rules_cover_regional_and_unknown_languages(say, heading):
    assert say("$5", lang="en-GB") == "five dollars"
    assert say("5 km", lang=None) == "five kilometers"
    assert heading("IV", lang=None) == "four"


# The sentence splitter runs first and must leave these whole.
def test_split_then_normalize(config, say):
    split = SentenceRules.from_config(config)
    text = "It cost $5.99 at 3:30 p.m. on Monday. Then we left at 10 a.m. Bring $1.50."
    sentences = split_sentences(text, split)
    assert sentences == [
        "It cost $5.99 at 3:30 p.m. on Monday.",
        "Then we left at 10 a.m.",
        "Bring $1.50.",
    ]
    assert [say(s) for s in sentences] == [
        "It cost five dollars and ninety-nine cents at three thirty p.m. on Monday.",
        "Then we left at ten a.m.",
        "Bring one dollar and fifty cents.",
    ]


@pytest.mark.parametrize(
    "text",
    [
        "It was published in 1886 by\nRobert Louis Stevenson.",
        "It was published in 1886 by \n Robert Louis Stevenson.",
        "It was published in 1886  by\r\n\tRobert   Louis Stevenson.",
    ],
)
def test_line_breaks_and_runs_of_spaces_are_one_space(say, text):
    assert say(text) == "It was published in eighteen eighty-six by Robert Louis Stevenson."


# The user's pronunciation files, set in config.


@pytest.fixture
def with_words(config, tmp_path):
    """Normalize with a pronunciation file holding `text`; returns (say, heading)."""

    def build(text):
        path = tmp_path / "words.toml"
        path.write_text(text, encoding="utf-8")
        custom = copy.deepcopy(config)
        custom["pronunciation"]["files"] = [str(path)]
        rules = SpeechRules.from_config(custom)
        return (
            lambda t: normalize_for_speech(t, rules, "en"),
            lambda t: normalize_heading(t, rules, "en"),
        )

    return build


def test_pronunciation_files_change_what_is_said(say, with_words):
    assert say("Utterson came.") == "Utterson came."
    said, _ = with_words('[words]\nUtterson = "utter son"\n')
    assert said("Utterson came.") == "Utter son came."


def test_sounds_like_comes_before_dogears_own_rewrites(say, with_words):
    said, _ = with_words('[words]\n"St. John" = "sin jun"\n"No. 5" = "number five, please"\n')
    assert said("St. John and St. Paul.") == "Sin jun and Saint Paul."
    assert said("Try No. 5 today.") == "Try Number five, please today."
    assert say("St. John.") == "Saint John."


def test_a_name_keeps_its_title_after_a_sounds_like_entry(with_words):
    # "Mr." becomes "Mister" only before a capitalized word.
    said, _ = with_words('[words]\nUtterson = "utter son"\n')
    assert said("Mr. Utterson's friend.") == "Mister Utter son's friend."


def test_the_spelling_is_normalized_like_any_text(with_words):
    said, _ = with_words('[words]\nR2D2 = "R 2 D 2"\n')
    assert said("R2D2 beeped.") == "R two D two beeped."


def test_phoneme_entries_are_left_for_the_voice(with_words):
    said, _ = with_words('[words]\nLanyon = { phonemes = "lˈænjən" }\n')
    assert said("Dr. Lanyon's house.") == "Doctor Lanyon's house."


def test_headings_use_pronunciation_files_too(with_words):
    _, heading = with_words('[words]\nUtterson = "utter son"\n')
    assert heading("CHAPTER IV: UTTERSON") == "CHAPTER four: Utter son"


def test_curly_apostrophes_in_the_book_match(with_words):
    said, _ = with_words('[words]\n"O\'Brien" = "oh bryan"\n')
    assert said("O’Brien’s cat.") == "Oh bryan's cat."
