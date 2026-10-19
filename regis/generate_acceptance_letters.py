#!/usr/bin/env python3
"""Generate presenting-author acceptance PDFs from the registration ODS export.

Uses only the Python standard library. Requires pdflatex and the LaTeX packages
used by acceptance_letter_template.tex. Run with --help for options.
"""

import argparse
from datetime import date, datetime
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

ROOT = Path(__file__).resolve().parent.parent
NS = {
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
}
HEADERS = {
    "timestamp": "Timestamp",
    "name": "Presenting author",
    "title": "Presentation title",
    "type": "Registration type",
    "institution": "Institution name (as should appeared in your receipt)",
    "department": "Department (as should appeared in your receipt)",
    "country": "Country of your institution",
}


def attr(namespace, name):
    return "{" + NS[namespace] + "}" + name


def cell_text(cell):
    """Read ODF text spans, explicit spaces, tabs, and paragraph breaks."""
    def walk(node):
        if node.tag == attr("text", "s"):
            return " " * min(int(node.get(attr("text", "c"), "1")), 1000)
        if node.tag in (attr("text", "tab"), attr("text", "line-break")):
            return " "
        return (node.text or "") + "".join(
            walk(child) + (child.tail or "") for child in node
        )

    paragraphs = cell.findall("text:p", NS)
    value = "\n".join(walk(p) for p in paragraphs).strip()
    return value or cell.get(attr("office", "date-value"), "")


def ods_rows(path, sheet_name=None):
    """Read the first (or named) sheet, honoring ODS repeated cells and rows."""
    with ZipFile(path) as archive:
        root = ET.fromstring(archive.read("content.xml"))
    sheets = root.findall("office:body/office:spreadsheet/table:table", NS)
    if sheet_name is not None:
        sheets = [s for s in sheets if s.get(attr("table", "name")) == sheet_name]
    if not sheets:
        raise ValueError("No matching worksheet found.")

    number = 1
    for row in sheets[0].findall(".//table:table-row", NS):
        values = []
        # Bound expansion: ODS may encode thousands of empty trailing columns.
        for cell in row:
            if cell.tag not in (attr("table", "table-cell"),
                                attr("table", "covered-table-cell")):
                continue
            repeat = int(cell.get(attr("table", "number-columns-repeated"), "1"))
            values.extend([cell_text(cell)] * min(repeat, 256 - len(values)))
            if len(values) == 256:
                break
        repeat = int(row.get(attr("table", "number-rows-repeated"), "1"))
        if any(values):
            if repeat > 10000:
                raise ValueError("Too many repeated nonempty spreadsheet rows.")
            for offset in range(repeat):
                yield number + offset, values
        number += repeat


def submission_time(value):
    """The Google Forms export uses month/day/year, even for ambiguous dates."""
    for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(f"Unsupported submission timestamp: {value!r}") from exc


def filename_name(name):
    name = unicodedata.normalize("NFC", name)
    name = re.sub(r"[^\w-]+", "_", name, flags=re.UNICODE).strip("_-")
    if not name:
        raise ValueError("Presenting author has no usable filename characters.")
    return name[:120]


def latex_escape(text):
    replacements = {
        "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$",
        "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}",
        "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(c, c) for c in re.sub(r"\s+", " ", text))


def latex_title(title):
    # Preserve simple inline mathematics such as the actual export's $q$.
    # Other spreadsheet text is escaped; arbitrary LaTeX commands are not run.
    allowed = set("alpha beta gamma delta epsilon varepsilon zeta eta theta "
                  "vartheta iota kappa lambda mu nu xi pi rho sigma tau upsilon "
                  "phi varphi chi psi omega Gamma Delta Theta Lambda Xi Pi Sigma "
                  "Upsilon Phi Psi Omega mathbb mathcal mathrm mathbf frac sqrt "
                  "infty leq geq neq in subset subseteq times cdot pm to rightarrow "
                  "langle rangle left right vert Vert norm sum prod int sup inf "
                  "min max lim operatorname".split())
    parts = re.split(r"(\$[^$\n]+\$)", title)
    result = []
    for part in parts:
        if part.startswith("$") and part.endswith("$") and len(part) > 2:
            math = part[1:-1]
            commands = re.findall(r"\\([A-Za-z]+|.)", math)
            if (all(c in allowed for c in commands)
                    and not re.search(r"[%#&~\x00-\x1f]", math)
                    and math.count("{") == math.count("}")):
                result.append("$" + math + "$")
                continue
        result.append(latex_escape(part))
    return "".join(result)


def submissions(path, sheet_name, selected_rows):
    rows = iter(ods_rows(path, sheet_name))
    _, headers = next(rows, (0, []))
    headers = [" ".join(h.split()) for h in headers]
    missing = [h for h in HEADERS.values() if h not in headers]
    if missing:
        raise ValueError("Missing spreadsheet columns: " + ", ".join(missing))
    columns = {key: headers.index(header) for key, header in HEADERS.items()}
    for key, header in {
        "email": "Email address (as should be contacted with)",
        "form_email": "Email Address",
    }.items():
        if header in headers:
            columns[key] = headers.index(header)
    registrant_header = "Full name (as should appeared in your receipt)"
    registrant_column = headers.index(registrant_header) if registrant_header in headers else None
    letters, skipped = [], []
    found_rows = set()
    filenames = set()
    for number, values in rows:
        if selected_rows and number not in selected_rows:
            continue
        found_rows.add(number)
        record = {key: values[col].strip() if col < len(values) else ""
                  for key, col in columns.items()}
        record["row"] = number
        if not record["type"].casefold().startswith("presenter"):
            # Non-presenters generally leave the Presenting author cell empty.
            registrant = (values[registrant_column].strip()
                          if registrant_column is not None and registrant_column < len(values)
                          else "")
            record["name"] = registrant or record["name"] or "[name not provided]"
            skipped.append(record)
            continue
        for field in ("name", "title", "timestamp", "institution"):
            if not record[field]:
                raise ValueError(f"Row {number}: missing {HEADERS[field]}.")
        stamp = submission_time(record["timestamp"]).strftime("%Y%m%d_%H%M%S")
        record["filename"] = f"{stamp}_{filename_name(record['name'])}.pdf"
        if record["filename"].casefold() in filenames:
            raise ValueError(f"Row {number}: duplicate timestamp/name filename.")
        filenames.add(record["filename"].casefold())
        letters.append(record)
    if selected_rows - found_rows:
        raise ValueError("Requested rows not found: " +
                         ", ".join(map(str, sorted(selected_rows - found_rows))))
    return letters, skipped


def render(template, record, letter_date):
    affiliation = r"\\".join(latex_escape(record[key])
                             for key in ("department", "institution", "country")
                             if record[key])
    fields = {
        "LETTER_DATE": latex_escape(letter_date.strftime("%B %d, %Y")),
        "PRESENTER": latex_escape(record["name"]),
        "AFFILIATION": affiliation,
        "TITLE": latex_title(record["title"]),
    }
    tokens = set(re.findall(r"@@([A-Z_]+)@@", template))
    if tokens != fields.keys():
        raise ValueError("Template must contain exactly these fields: " +
                         ", ".join(sorted(fields)))
    return re.sub(r"@@([A-Z_]+)@@", lambda m: fields[m[1]], template)


def log_record(log, status, record):
    """Write a readable, single-line entry and retain it if a later PDF fails."""
    if log is None:
        return
    clean = lambda value: " ".join(str(value).split())
    entry = f"{status} | Row {record['row']} | Name: {clean(record['name'])}"
    if status == "SKIPPED NON-PRESENTER":
        entry += f" | Registration: {clean(record['type'])}"
    else:
        entry += f" | Title: {clean(record['title'])} | PDF: {record['filename']}"
    log.write(entry + "\n")
    log.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ods", type=Path, nargs="?", default=ROOT / "regis/reg.ods",
                        help="ODS registration export (default: regis/reg.ods)")
    parser.add_argument("--template", type=Path,
                        default=Path(__file__).with_name("acceptance_letter_template.tex"))
    parser.add_argument("--output-dir", type=Path, default=ROOT / "acceptance_letters")
    parser.add_argument("--sheet", help="Worksheet name; defaults to the first sheet")
    parser.add_argument("--row", type=int, action="append", default=[],
                        help="Only process this spreadsheet row (header is row 1); repeatable")
    parser.add_argument("--date", type=date.fromisoformat, default=date.today(),
                        help="Letter issue date YYYY-MM-DD (default: today)")
    parser.add_argument("--overwrite", action="store_true", help="Replace existing PDFs")
    parser.add_argument("--dry-run", action="store_true", help="List PDFs without writing files")
    args = parser.parse_args()
    log = None
    try:
        records, skipped = submissions(args.ods, args.sheet, set(args.row))
        template = args.template.read_text(encoding="utf-8")
        rendered = [(r, render(template, r, args.date)) for r in records]
        if not args.dry_run and not shutil.which("pdflatex"):
            raise ValueError("pdflatex is required; install a LaTeX distribution first.")
        output_dir = args.output_dir.resolve()
        if not args.dry_run:
            output_dir.mkdir(parents=True, exist_ok=True)
            log = (output_dir / "generation.log").open("a", encoding="utf-8")
            log.write(f"\nRun: {datetime.now().astimezone().isoformat(timespec='seconds')}\n"
                      f"Spreadsheet: {args.ods.resolve()}\n")
            for record in skipped:
                log_record(log, "SKIPPED NON-PRESENTER", record)
        generated = existing = 0
        for record, source in rendered:
            destination = output_dir / record["filename"]
            if destination.exists() and not args.overwrite:
                print(f"Already exists: {destination.name}")
                log_record(log, "SKIPPED EXISTING PDF", record)
                existing += 1
                continue
            if args.dry_run:
                print(f"Row {record['row']}: {destination.name}")
                continue
            output_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="icnaao-acceptance-") as temp:
                work = Path(temp)
                tex = work / "letter.tex"
                tex.write_text(source, encoding="utf-8")
                command = ["pdflatex", "-no-shell-escape", "-interaction=nonstopmode",
                           "-halt-on-error", f"-output-directory={work}", str(tex)]
                proc = subprocess.run(command, cwd=ROOT, capture_output=True,
                                      text=True, errors="replace", timeout=120)
                if proc.returncode:
                    raise ValueError(f"Row {record['row']}: LaTeX compilation failed.\n" +
                                     "\n".join(proc.stdout.splitlines()[-25:]))
                shutil.copyfile(work / "letter.pdf", destination)
            generated += 1
            log_record(log, "GENERATED", record)
            print(f"Created: {destination}")
        summary = (f"{len(records)} presenter(s); {len(skipped)} non-presenter(s) skipped; "
                   f"{generated} PDF(s) created; {existing} existing PDF(s) left unchanged.")
        print(summary)
        if log is not None:
            log.write(summary + "\n")
            print(f"Log: {output_dir / 'generation.log'}")
        return 0
    except (OSError, ValueError, BadZipFile, ET.ParseError, KeyError,
            subprocess.TimeoutExpired) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if log is not None:
            log.write(f"ERROR: {exc}\n")
        return 1
    finally:
        if log is not None:
            log.close()


if __name__ == "__main__":
    sys.exit(main())
