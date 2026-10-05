"""Person names for the four-eyes rules.

Identity is still self-asserted (see the README), but "alice", "Alice", "ALICE " and "alice" with a zero-width
character are obviously one person, so they must compare equal.
"""
import re
import unicodedata


def clean_name(name) -> str:
    """Display form: Unicode-normalised, invisible format characters removed, whitespace collapsed."""
    text = unicodedata.normalize("NFKC", str(name or ""))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    return re.sub(r"\s+", " ", text).strip()


def same_person(a, b) -> bool:
    ka, kb = clean_name(a).casefold(), clean_name(b).casefold()
    return bool(ka) and ka == kb
