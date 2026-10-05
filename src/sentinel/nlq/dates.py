"""Deterministic calendar interpretation; all internal end boundaries are exclusive.

Relative periods use the supplied reference, never the host clock. Interpretation
and historical-query eligibility are separate: next month resolves but is not an
observed-delivery window. A daily/weekly request is never silently coarsened.
"""

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

from sentinel.config import AS_OF

MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
MONTH = "(?:" + "|".join(MONTHS) + ")"


@dataclass(frozen=True)
class DateWindow:
    start: str
    end: str
    granularity: str

    def boundaries(self):
        return {"start": self.start, "end": self.end}


def month_end(year, month):
    return date(year + (month == 12), 1 if month == 12 else month + 1, 1)


def extract_date_range(text, reference=AS_OF):
    today = date.fromisoformat(reference)
    q = text.casefold()
    patterns = [
        (rf"calendar month before ({MONTH}) (\d{{4}})", "before"),
        (r"\b(last|previous|this|next) (?:calendar )?month\b", "relative"),
        (r"\b(?:last|previous) quarter\b", "previous_quarter"),
        (r"\bpast (\d+) days\b", "rolling"),
        (rf"first (\d+) days of ({MONTH}) (\d{{4}})", "prefix"),
        (r"(first|second|third|fourth) quarter of (\d{4})", "quarter"),
        (
            r"(\d{4}-\d{2}-\d{2})\s+(?:through|to)\s+(\d{4}-\d{2}-\d{2})(?:\s+(inclusive|exclusive))?",
            "iso",
        ),
        (
            rf"({MONTH}) (\d+) and ({MONTH}) (\d+) inclusive\??[.;]? use (\d{{4}}) dates",
            "named_days",
        ),
        (rf"({MONTH})(?: of)? (\d{{4}})", "month"),
    ]
    # Prefer the complete expression to component month/year matches.
    selected = []
    for pattern, kind in patterns:
        for match in re.finditer(pattern, q):
            if not any(match.start() < m.end() and match.end() > m.start() for m, _ in selected):
                selected.append((match, kind))
    if len(selected) != 1:
        raise ValueError("Exactly one unambiguous date range with a year is required")
    match, kind = selected[0]
    granularity = "range"
    if kind in {"before", "relative"}:
        anchor = (
            date(int(match[2]), MONTHS[match[1]], 1) if kind == "before" else today.replace(day=1)
        )
        offset = (
            -1
            if kind == "before" or match[1] in {"last", "previous"}
            else 1
            if match[1] == "next"
            else 0
        )
        start = (
            (anchor - timedelta(days=1)).replace(day=1)
            if offset == -1
            else month_end(anchor.year, anchor.month)
            if offset == 1
            else anchor
        )
        end = month_end(start.year, start.month)
        granularity = "month"
    elif kind == "previous_quarter":
        end = date(today.year, ((today.month - 1) // 3) * 3 + 1, 1)
        last = end - timedelta(days=1)
        start = date(last.year, ((last.month - 1) // 3) * 3 + 1, 1)
        granularity = "quarter"
    elif kind == "rolling":
        days = int(match[1])
        if not 1 <= days <= 366:
            raise ValueError("Rolling history must contain 1 to 366 days")
        # Past N completed days excludes the reference day.
        start, end = today - timedelta(days=days), today
        granularity = "range"
    elif kind == "prefix":
        start = date(int(match[3]), MONTHS[match[2]], 1)
        end = date(start.year, start.month, int(match[1])) + timedelta(days=1)
    elif kind == "quarter":
        month = 1 + 3 * ("first second third fourth".split().index(match[1]))
        start = date(int(match[2]), month, 1)
        end = month_end(start.year, month + 2)
        granularity = "quarter"
    elif kind == "iso":
        start, end = date.fromisoformat(match[1]), date.fromisoformat(match[2])
        end += timedelta(days=match[3] != "exclusive")
    elif kind == "named_days":
        start = date(int(match[5]), MONTHS[match[1]], int(match[2]))
        end = date(int(match[5]), MONTHS[match[3]], int(match[4])) + timedelta(days=1)
    else:
        start = date(int(match[2]), MONTHS[match[1]], 1)
        end = month_end(start.year, start.month)
        granularity = "month"
    if start >= end:
        raise ValueError("Date range must be nonempty and increasing")
    remaining = q[: match.start()] + " " + q[match.end() :]
    grains = re.findall(r"\b(daily|weekly) history\b", remaining)
    if len(grains) > 1:
        raise ValueError("Ambiguous history granularity")
    if grains:
        granularity = {"daily": "day", "weekly": "week"}[grains[0]]
        remaining = re.sub(r"\b(?:daily|weekly) history\b", " ", remaining)
    return DateWindow(start.isoformat(), end.isoformat(), granularity), remaining


def historical_window(text, reference=AS_OF):
    window, rest = extract_date_range(text, reference)
    if date.fromisoformat(window.end) > date.fromisoformat(reference) + timedelta(days=1):
        raise ValueError("Historical evidence cannot include future dates")
    return window, rest
