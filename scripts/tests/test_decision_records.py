"""The decision records under docs/tdr hold their shape and their index.

A record nobody can find from the index, or a superseded one that does
not say what replaced it, sends the next reader back to the argument the
record was written to end.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TDR = ROOT / "docs" / "tdr"
INDEX = TDR / "README.md"

FILE_NAME = re.compile(r"(\d{4})-[a-z0-9]+(?:-[a-z0-9]+)*\.md")
TITLE = re.compile(r"# TDR-(\d{4}): (\S.*)")
STATUS = re.compile(r"Proposed|Accepted|Superseded by TDR-(\d{4})")
SUPERSEDES = re.compile(r"TDR-(\d{4})")
INDEX_ROW = re.compile(r"\| \[(\d{4})\]\(([^)]+)\) \| (.+?) \| (.+?) \|")
HEADINGS = ["Context", "Decision", "Consequences", "Revisit when"]


@dataclass(frozen=True)
class Record:
    path: Path
    number: str
    title: str
    status: str
    date: datetime.date
    supersedes: tuple[str, ...]
    sections: dict[str, str]


def _parse(path: Path) -> Record:
    lines = path.read_text(encoding="utf-8").splitlines()
    title = TITLE.fullmatch(lines[0])
    assert title, f"{path.name}: the first line is not '# TDR-NNNN: <title>'"
    fields: dict[str, str] = {}
    body_start = next((i for i, line in enumerate(lines) if line.startswith("## ")), len(lines))
    for line in lines[1:body_start]:
        if line.startswith("- "):
            key, sep, value = line[2:].partition(": ")
            assert sep, f"{path.name}: '{line}' is not '- Key: value'"
            assert key not in fields, f"{path.name}: {key} appears twice"
            fields[key] = value
        else:
            assert not line.strip(), f"{path.name}: stray line before the first heading: '{line}'"
    unknown = set(fields) - {"Status", "Date", "Supersedes"}
    assert not unknown, f"{path.name}: unknown fields {sorted(unknown)}"
    assert "Status" in fields, f"{path.name}: no '- Status:' line"
    assert "Date" in fields, f"{path.name}: no '- Date:' line"

    sections: dict[str, str] = {}
    heading = None
    for line in lines[body_start:]:
        if line.startswith("## "):
            heading = line[3:]
            assert heading not in sections, f"{path.name}: heading '{heading}' appears twice"
            sections[heading] = ""
        elif heading is not None:
            sections[heading] += line + "\n"

    return Record(
        path=path,
        number=title.group(1),
        title=title.group(2),
        status=fields["Status"],
        date=datetime.date.fromisoformat(fields["Date"]),
        supersedes=tuple(SUPERSEDES.findall(fields.get("Supersedes", ""))),
        sections={k: v.strip() for k, v in sections.items()},
    )


def _record_paths() -> list[Path]:
    return sorted(p for p in TDR.iterdir() if p.name != "README.md")


RECORDS = {record.number: record for record in map(_parse, _record_paths())}


def test_only_records_and_the_index_live_in_the_directory():
    strays = [p.name for p in _record_paths() if not FILE_NAME.fullmatch(p.name)]
    assert not strays, f"not named NNNN-short-slug.md: {strays}"


def test_numbers_run_in_sequence_from_one():
    numbers = [FILE_NAME.fullmatch(p.name).group(1) for p in _record_paths()]
    expected = [f"{n:04d}" for n in range(1, len(numbers) + 1)]
    assert numbers == expected, "a record number is missing, doubled or out of sequence"


@pytest.mark.parametrize("record", RECORDS.values(), ids=lambda r: r.path.name)
def test_title_carries_the_file_number(record):
    assert record.path.name.startswith(f"{record.number}-")


@pytest.mark.parametrize("record", RECORDS.values(), ids=lambda r: r.path.name)
def test_headings_are_the_four_in_order_and_filled(record):
    assert list(record.sections) == HEADINGS
    empty = [h for h, text in record.sections.items() if not text]
    assert not empty, f"{record.path.name}: empty sections {empty}"


@pytest.mark.parametrize("record", RECORDS.values(), ids=lambda r: r.path.name)
def test_status_is_a_known_one(record):
    assert STATUS.fullmatch(record.status), f"{record.path.name}: unknown status '{record.status}'"


@pytest.mark.parametrize("record", RECORDS.values(), ids=lambda r: r.path.name)
def test_date_is_not_in_the_future(record):
    assert record.date <= datetime.datetime.now(tz=datetime.UTC).date()


@pytest.mark.parametrize("record", RECORDS.values(), ids=lambda r: r.path.name)
def test_a_superseded_record_names_a_successor_that_names_it_back(record):
    status = STATUS.fullmatch(record.status)
    successor = status.group(1) if status else None
    if successor is None:
        return
    assert successor in RECORDS, f"{record.path.name}: TDR-{successor} does not exist"
    assert record.number in RECORDS[successor].supersedes, (
        f"TDR-{successor} does not say '- Supersedes: TDR-{record.number}'"
    )


@pytest.mark.parametrize("record", RECORDS.values(), ids=lambda r: r.path.name)
def test_a_superseding_record_is_named_back_by_what_it_replaced(record):
    for old in record.supersedes:
        assert old in RECORDS, f"{record.path.name}: TDR-{old} does not exist"
        assert RECORDS[old].status == f"Superseded by TDR-{record.number}", (
            f"TDR-{old} still says '{RECORDS[old].status}'"
        )


def test_the_index_lists_every_record_once_with_its_title_and_status():
    lines = INDEX.read_text(encoding="utf-8").splitlines()
    listed = [m.groups() for m in map(INDEX_ROW.fullmatch, lines) if m]
    expected = [(r.number, r.path.name, r.title, r.status) for r in RECORDS.values()]
    assert listed == expected
