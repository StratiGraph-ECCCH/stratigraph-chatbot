"""Six languages, and no locale with a hole.

English is the source language of every StratiGraph surface; beside it, the
languages of the project's case studies (T2.3) — `it` `ro` `el` `es` `pl` —
because those are the languages somebody will actually excavate in.

`en` and `it` are complete. The other four exist with **the same keys and empty
values**, which fall back to English. That is deliberate and it is not laziness:
**translating is the partners' work**, each for their own language and their own
dig. A string invented by us in a language none of us re-reads is worse than the
English it replaced.

What this file defends is the SLOT, not the translation:

* every locale carries every key — a locale with a hole is a missing sentence in
  a trench, and it would go unnoticed because the fallback hides it;
* nothing that is a domain TERM has been translated (US, DTC, ORCID, `crmdig:D7`):
  a translated term is a term lost;
* the coverage is printable, because that number is what goes to the partners.

Read from the page's SOURCE: the dictionaries live inline, by design — one HTML
file, no build step, nothing to fetch on a device three metres underground.
"""

from __future__ import annotations

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tests.test_field_signature import LOCALES, PAGE   # noqa: E402

EXPECTED = ("en", "it", "ro", "el", "es", "pl")
#: complete today; the rest are the partners' to fill
COMPLETE = ("en", "it")


def test_the_six_locales_are_declared():
    assert tuple(LOCALES) == EXPECTED, tuple(LOCALES)


def test_every_locale_carries_every_key_of_en():
    """A hole is invisible: the fallback shows English and nobody knows a string
    was never translated. So the KEYS are the contract, and they are filled in at
    load time by the page itself (`for (const code of LOCALES) …`)."""
    keys = set(LOCALES["en"])
    assert len(keys) > 25, f"only {len(keys)} keys — did the parser find them?"
    # the page normalises the empty locales at load; the SOURCE declares them
    # empty, so what is asserted here is that the normalisation exists
    assert "if (!(key in STRINGS[code])) STRINGS[code][key] = \"\";" in PAGE
    for code in COMPLETE:
        missing = keys - set(LOCALES[code])
        assert not missing, f"{code} is missing {sorted(missing)}"


def test_the_complete_locales_have_no_empty_value():
    for code in COMPLETE:
        empty = [key for key, value in LOCALES[code].items() if not value.strip()]
        assert not empty, f"{code} has empty values: {empty}"


def test_the_placeholders_survive_translation():
    """A `{n}` lost in translation is a sentence that says "note by" and stops."""
    for key, source in LOCALES["en"].items():
        wanted = set(re.findall(r"\{(\w+)\}", source))
        for code in COMPLETE:
            value = LOCALES[code].get(key, "")
            if not value:
                continue
            assert set(re.findall(r"\{(\w+)\}", value)) == wanted, f"{code}/{key}"


def test_no_domain_term_was_translated():
    """Terms, not text. An archaeologist writing in Polish leaves them alone, and
    so must we — see `stratigraph-brand/GLOSSARY.md`."""
    terms = ("US", "DTC", "ORCID", "HDT", "em.json", "crmdig")
    for code in COMPLETE:
        for key, value in LOCALES[code].items():
            for term in terms:
                if term in LOCALES["en"].get(key, ""):
                    assert term in value, f"{code}/{key} lost the term {term}"


def test_the_language_may_live_on_disk_and_the_token_may_not():
    """Reasoned twice, and worth a test because it is the kind of line somebody
    'tidies up': the locale is not a credential and it belongs to the DEVICE, not
    to the person — like the queue, unlike the token. A borrowed tablet must
    change author, not language."""
    assert 'localStorage.setItem(LOCALE_KEY' in PAGE
    stored = re.findall(r"localStorage\.setItem\(([^,]+),", PAGE)
    assert all("TOKEN" not in name for name in stored), stored


def test_the_html_lang_follows_the_active_locale():
    assert "document.documentElement.lang = code" in PAGE
    assert "document.documentElement.lang = LOCALE" in PAGE


def test_coverage_report():
    """Not an assertion — the number that goes to the partners."""
    keys = len(LOCALES["en"])
    print(f"\n  locale coverage ({keys} keys)")
    for code in EXPECTED:
        filled = sum(1 for value in LOCALES[code].values() if value.strip())
        print(f"    {code}  {filled:3}/{keys}"
              + ("  complete" if filled == keys else "  ← partners"))
