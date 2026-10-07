"""Interface languages. English is the source language and is written in the page itself: every fixed piece of program text goes
through t('English sentence {0}', value) in page.py, and the English sentence is the key. A language here is a table from those
sentences to its own wording, keeping the {0}, {1} placeholders. A sentence that is missing from a table is shown in English, never
as a blank or an error.

Only program text is translated. Column names, file names, notes and a model's answers are data and are shown as they are.
Not translated (yet): the files a run writes (report.md, quarantine reasons, the audit log), the command line, and the reasons
the checks give for one row; they stay English so that a report reads the same wherever it is opened.
"""
import json

from .i18n_uk import UK

LANGUAGES = ("system", "en", "uk")
TABLES = {"uk": UK}


def embedded(language="system"):
    """The tables as a JSON object that is safe inside an inline <script>. Choosing English needs none."""
    if language == "en":
        return "{}"
    text = json.dumps(TABLES["uk"], ensure_ascii=False, separators=(",", ":"))
    for raw, safe in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"), ("/", "\\/"),
                      (chr(0x2028), "\\u2028"), (chr(0x2029), "\\u2029")):       # line separators end a line in older JavaScript
        text = text.replace(raw, safe)
    return text
