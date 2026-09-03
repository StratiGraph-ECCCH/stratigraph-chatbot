"""What this build calls itself.

The number is repeated in two places that cannot import each other —
`pyproject.toml` is static metadata and `app/main.py` is what the service
reports about itself — so a repetition without a guard is a divergence with a
date on it.

And the convention is not this service's to invent:

    <major EM>.<minor EM>.<the tool's own iteration>

**A tool cannot be more stable than the language it speaks**, so the first two
segments are s3Dgraphy's. Read off the installed library rather than typed here:
a number typed in a test is a number that agrees with itself for ever.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from app.main import __version__

REPO = pathlib.Path(__file__).resolve().parent.parent


def test_pyproject_and_the_module_agree():
    text = (REPO / "pyproject.toml").read_text()
    found = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    assert found, "pyproject.toml declares no version"
    assert found.group(1) == __version__, (
        f"pyproject.toml says {found.group(1)!r} and app/main.py says "
        f"{__version__!r}. One is what gets installed, the other is what the "
        f"service reports about itself.")


def test_the_first_two_segments_are_the_language_s_own():
    import s3dgraphy

    library = getattr(s3dgraphy, "__version__", "")
    if not library:
        pytest.skip("this s3dgraphy declares no __version__")
    assert __version__.split(".")[:2] == library.split(".")[:2], (
        f"this build calls itself {__version__} while it speaks s3Dgraphy "
        f"{library}. A tool cannot be more stable than its language.")


def test_it_is_no_longer_the_never_versioned_default():
    assert __version__ != "0.1.0.dev0"
    assert not __version__.startswith("0."), (
        "a 0.x version says this tool speaks Extended Matrix 0.x, which is not "
        "a language that exists")
