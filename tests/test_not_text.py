"""A file that is not text is named for what it is (so the person knows what to do), by both readers, and a real text file is never
refused because its first letters happen to look like a signature. A column name from the file is as untrusted as a cell."""
import gzip

import pytest

from conftest import CLEAN_CSV
from datapipe.errors import IngestError
from datapipe.ingest import binary_kind, read_source

HEAD = "order_id,customer_email,region,amount,order_date,paid\n"
ROW = "1,a@b.co,EU,10.50,2024-03-01,true\n"

CASES = {
    "xlsx": (b"PK\x03\x04" + b"\x14\x00\x06\x00" * 20, "Excel .xlsx"),
    "gzip": (gzip.compress((HEAD + ROW).encode()), "gzip-compressed"),
    "gzip-tiny-without-nul": (b"\x1f\x8b\x08\x08\xd3\x7a\x9c\x65\x02\xff" + b"\xab\xcd" * 8, "gzip-compressed"),
    "legacy-xls": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 60, "old Excel or Word"),
    "utf16-le": (b"\xff\xfe" + (HEAD + ROW).encode("utf-16-le")[:] , "UTF-16 or UTF-32"),
    "utf16-be": (b"\xfe\xff" + (HEAD + ROW).encode("utf-16-be"), "UTF-16 or UTF-32"),
    "utf32-le": (b"\xff\xfe\x00\x00" + (HEAD + ROW).encode("utf-32-le"), "UTF-16 or UTF-32"),
    "sqlite": (b"SQLite format 3\x00" + b"\x00" * 80, "SQLite database"),
    "pdf": (b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\x00\x00", "PDF document"),
    "parquet": (b"PAR1\x15\x00\x15\x04" + b"\x00" * 30 + b"PAR1", "Parquet"),
    "png": (b"\x89PNG\r\n\x1a\n" + b"\x00" * 30, "image"),
    "jpeg": (b"\xff\xd8\xff\xe0\x00\x10JFIF", "image"),
    "gif": (b"GIF89a\x01\x00\x01\x00\x00\x00", "image"),
    "bzip2": (b"BZh91AY&SY\x00\x00", "compressed archive"),
    "7z": (b"7z\xbc\xaf\x27\x1c\x00\x04" + b"\x00" * 20, "compressed archive"),
    "zst": (b"\x28\xb5\x2f\xfd\x20\x00", "compressed archive"),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_a_file_that_is_not_text_is_named_for_what_it_is_by_both_readers(name, write, run):
    data, said = CASES[name]
    path = write(f"{name}.csv", data)                                   # renamed to .csv: the user's file name proves nothing
    with pytest.raises(IngestError) as caught:
        read_source(path, max_bytes=10 ** 8)
    assert said in str(caught.value) and "NUL bytes" not in str(caught.value)
    for stream in ("never", "always"):
        res = run(path, stream=stream)
        assert res.status == "FAILED" and said in res.reasons[0], (stream, res.reasons)


def test_an_unrecognised_binary_file_still_gets_the_general_message(write, run):
    path = write("odd.csv", b"id,name\n1,a\x00b\n")
    with pytest.raises(IngestError, match="NUL bytes"):
        read_source(path, max_bytes=10 ** 8)
    assert "NUL bytes" in run(path, stream="always").reasons[0]


@pytest.mark.parametrize("first_cell", ["PAR1", "GIF89a", "BZh9", "%PDF-1", "SQLite format 3", "PK"])
def test_a_real_text_file_whose_first_letters_look_like_a_signature_is_still_read(first_cell, write, run):
    path = write("text.csv", f"{first_cell},customer_email,region,amount,order_date,paid\n1,a@b.co,EU,10.50,2024-03-01,true\n")
    assert binary_kind(path.read_bytes()[:16], False) is None
    for stream in ("never", "always"):
        res = run(path, stream=stream)
        assert res.status == "BLOCKED" and "required columns missing" in res.reasons[0], (first_cell, stream, res.reasons)   # read fine; the schema lacks the column


def test_a_plain_utf8_file_with_a_byte_order_mark_is_unaffected(write, run):
    path = write("bom.csv", b"\xef\xbb\xbf" + CLEAN_CSV.encode())
    for stream in ("never", "always"):
        assert run(path, stream=stream).status == "COMPLETED"


def test_the_header_row_of_quarantine_csv_is_neutralised_like_the_cells(write, run):
    """A column name comes from the file. Excel runs a header cell like any other: =HYPERLINK(...) in row 1 would be a live link."""
    evil = '"=HYPERLINK(""http://evil.example"",""click"")",@SUM(1),+cmd,-2+3,-5,normal'
    path = write("h.csv", HEAD.rstrip("\n") + "," + evil + "\n" + ROW.rstrip("\n") + ",x,y,z,w,v,u\n"
                 + "2,bad,EU,10.50,2024-03-01,true,x,y,z,w,v,u\n")
    for stream in ("never", "always"):
        res = run(path, stream=stream)
        assert res.document["counts"]["quarantined"] == 1
        first = (res.run_dir / "quarantine.csv").read_text(encoding="utf-8").splitlines()[0]
        assert first.startswith("row,rules,messages,order_id,")
        assert "\"'=HYPERLINK(" in first and ",'@SUM(1)" in first and ",'+cmd" in first and ",'-2+3" in first    # the quote that guards the cell comes first
        assert ",-5," in first and first.endswith(",normal")               # a plain number or a plain name is shown as it is
        assert ",=HYPERLINK" not in first and '"=HYPERLINK' not in first
