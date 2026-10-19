"""Run: python3 -m unittest discover -s regis -p 'test_*.py'."""

from datetime import date
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import generate_acceptance_letters as letters


class AcceptanceLettersTests(unittest.TestCase):
    def test_ods_repeated_cells_and_rich_text(self):
        content = '''<office:document-content
          xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
          xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
          xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">
          <office:body><office:spreadsheet><table:table table:name="Registrations">
            <table:table-row>
              <table:table-cell><text:p>A<text:s text:c="2"/><text:span>B</text:span></text:p></table:table-cell>
              <table:table-cell table:number-columns-repeated="2"/>
              <table:table-cell><text:p>Title</text:p><text:p>continued</text:p></table:table-cell>
            </table:table-row>
            <table:table-row table:number-rows-repeated="2">
              <table:table-cell><text:p>Repeated</text:p></table:table-cell>
            </table:table-row>
            <table:table-row table:number-rows-repeated="1000000">
              <table:table-cell table:number-columns-repeated="16384"/>
            </table:table-row>
          </table:table></office:spreadsheet></office:body>
        </office:document-content>'''
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "test.ods"
            with ZipFile(path, "w") as archive:
                archive.writestr("content.xml", content)
            self.assertEqual(list(letters.ods_rows(path, "Registrations")), [
                (1, ["A  B", "", "", "Title\ncontinued"]),
                (2, ["Repeated"]), (3, ["Repeated"]),
            ])
            with self.assertRaisesRegex(ValueError, "worksheet"):
                list(letters.ods_rows(path, "Missing"))

    def test_presenting_author_and_non_presenter_filter(self):
        rows = [
            (1, list(letters.HEADERS.values()) + ["Full name (as should appeared in your receipt)"]),
            (2, ["10/6/2026 20:51:54", "", "", "Participant without presentation",
                 "Example University", "Mathematics", "Thailand", "Non-presenting Registrant"]),
            (3, ["10/14/2026 18:25:42", "Presenting Author", "A title",
                 "Presenter (Student)", "Example University", "Mathematics", "India"]),
        ]
        with patch.object(letters, "ods_rows", return_value=iter(rows)):
            records, skipped = letters.submissions(Path("unused"), None, set())
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["name"], "Non-presenting Registrant")
        self.assertEqual(skipped[0]["row"], 2)
        self.assertEqual(records[0]["name"], "Presenting Author")
        self.assertEqual(records[0]["filename"], "20261014_182542_Presenting_Author.pdf")
        with patch.object(letters, "ods_rows", return_value=iter(rows)):
            records, skipped = letters.submissions(Path("unused"), None, {3})
        self.assertEqual((len(records), len(skipped)), (1, 0))

    def test_log_includes_generated_names_titles_and_skip_reasons(self):
        log = StringIO()
        record = {"row": 3, "name": "An Author", "title": "A title\nwith a newline",
                  "filename": "20261014_182542_An_Author.pdf"}
        letters.log_record(log, "GENERATED", record)
        letters.log_record(log, "SKIPPED EXISTING PDF", record)
        letters.log_record(log, "SKIPPED NON-PRESENTER",
                           {"row": 2, "name": "A Registrant", "type": "Participant"})
        lines = log.getvalue().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertIn("GENERATED | Row 3 | Name: An Author | Title: A title with a newline", lines[0])
        self.assertIn("SKIPPED EXISTING PDF", lines[1])
        self.assertIn("SKIPPED NON-PRESENTER | Row 2 | Name: A Registrant", lines[2])

    def test_invalid_presenter_fails_before_generation(self):
        rows = [(1, list(letters.HEADERS.values())),
                (2, ["10/14/2026 18:25:42", "Author", "", "Presenter (General)",
                     "University", "Mathematics", "India"])]
        with patch.object(letters, "ods_rows", return_value=iter(rows)):
            with self.assertRaisesRegex(ValueError, "Row 2: missing Presentation title"):
                letters.submissions(Path("unused"), None, set())

    def test_title_math_keeps_spaces_and_escapes_unsafe_commands(self):
        self.assertEqual(letters.latex_title("In $q$-spaces & 50% results"),
                         r"In $q$-spaces \& 50\% results")
        self.assertNotIn(r"\input{", letters.latex_title(r"Title $\input{secret}$"))
        self.assertEqual(letters.latex_escape("A_B #1"), r"A\_B \#1")

    def test_date_and_filename(self):
        self.assertEqual(letters.submission_time("10/6/2026 20:51:54").month, 10)
        self.assertEqual(letters.submission_time("2026-10-06T20:51:54").day, 6)
        self.assertEqual(letters.filename_name("../A / B"), "A_B")
        with self.assertRaises(ValueError):
            letters.submission_time("not a date")

    def test_rendering_does_not_reinterpret_presenter_text_as_tokens(self):
        template = "@@PRESENTER@@ @@TITLE@@ @@AFFILIATION@@ @@LETTER_DATE@@"
        record = {"name": "A & B", "title": "Title", "department": "Math",
                  "institution": "University", "country": "India"}
        rendered = letters.render(template, record, date(2026, 10, 19))
        self.assertIn(r"A \& B", rendered)
        self.assertIn(r"Math\\University\\India", rendered)
        self.assertIn("October 19, 2026", rendered)
        with self.assertRaises(ValueError):
            letters.render("@@UNKNOWN@@", record, date.today())


if __name__ == "__main__":
    unittest.main()
