"""
Excel report generation for accommodation audit results.

Uses pandas + xlsxwriter for fast bulk writes. Conditional formatting
is applied as a range-level rule rather than per-cell so performance
scales well even at tens of thousands of rows.

Extensibility
-------------
To add a column, append a ``(column_name, extractor)`` tuple to
``_COLUMN_SPEC``. The extractor receives an ``AuditRow`` and returns
the cell value. No other changes are needed.

Usage
-----
    from audit.reporting import write_xlsx
    from pathlib import Path

    write_xlsx(rows, Path("report.xlsx"))
    write_xlsx(rows, Path("report.xlsx"), show_progress=True)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

import pandas as pd

from audit.models.audit import AuditRow

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------

_GREEN_HEX = "#C6EFCE"
_YELLOW_HEX = "#FFEB9C"
_HEADER_BG = "#4472C4"
_HEADER_FG = "#FFFFFF"

# ---------------------------------------------------------------------------
# Column spec
# ---------------------------------------------------------------------------
# Each entry is (column_name, extractor_function).
# To add a column later, append a tuple here — no other changes needed.

_COLUMN_SPEC: list[tuple[str, Callable[[AuditRow], object]]] = [
    # --- Term context (Bucket 2) ---
    ("enrollment_term_id",      lambda r: r.enrollment_term_id),
    ("term_name",               lambda r: r.term_name),
    # --- Course context (Bucket 1) ---
    ("course_id",               lambda r: r.course_id),
    ("course_name",             lambda r: r.course_name),
    ("course_code",             lambda r: r.course_code),
    ("sis_course_id",           lambda r: r.sis_course_id),
    # --- Quiz context (Bucket 1) ---
    ("quiz_id",                 lambda r: r.quiz_id),
    ("quiz_title",              lambda r: r.quiz_title),
    ("quiz_due_at",             lambda r: r.quiz_due_at),
    ("quiz_lock_at",            lambda r: r.quiz_lock_at),
    # --- Audit identity ---
    ("engine",                  lambda r: r.engine),
    ("accommodation_type",      lambda r: r.accommodation_type.value if r.accommodation_type else None),
    # --- User context (Bucket 2) ---
    ("user_id",                 lambda r: r.user_id),
    ("user_name",               lambda r: r.user_name),
    ("sis_user_id",             lambda r: r.sis_user_id),
    ("item_id",                 lambda r: r.item_id),
    # --- Audit result ---
    ("has_accommodation",       lambda r: r.has_accommodation),
    ("completed",               lambda r: r.completed),
    ("attempts_left",           lambda r: r.attempts_left),
    # --- Accommodation details ---
    ("extra_time",              lambda r: r.details.get("extra_time")),
    ("extra_time_in_seconds",   lambda r: r.details.get("extra_time_in_seconds")),
    ("timer_multiplier_value",  lambda r: r.details.get("timer_multiplier_value")),
    ("extra_attempts",          lambda r: r.details.get("extra_attempts")),
    ("spell_check",             lambda r: r.details.get("spell_check")),
    ("position",                lambda r: r.details.get("position")),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def write_xlsx(
    rows: list[AuditRow],
    output_path: Path,
    *,
    show_progress: bool = False,
) -> None:
    """
    Write audit rows to an Excel workbook at *output_path*.

    Parameters
    ----------
    rows:
        Audit results to write. An empty sheet with headers is written
        when rows is empty.
    output_path:
        Destination ``.xlsx`` file. Parent directory must exist.
    show_progress:
        If True, show a tqdm progress bar during DataFrame construction
        and the write step.

    Raises
    ------
    OSError
        If the file cannot be written.
    """
    output_path = Path(output_path)
    col_names = [col for col, _ in _COLUMN_SPEC]

    # --- Build DataFrame ---
    if show_progress:
        from tqdm import tqdm
        iterable = tqdm(rows, desc="Building report", unit="rows", leave=False)
    else:
        iterable = rows

    data = {col: [] for col in col_names}
    for row in iterable:
        for col_name, extractor in _COLUMN_SPEC:
            data[col_name].append(extractor(row))

    df = pd.DataFrame(data, columns=col_names)

    # --- Write to Excel via xlsxwriter ---
    if show_progress:
        from tqdm import tqdm
        with tqdm(total=3, desc="Writing Excel", unit="step", leave=False) as pbar:
            writer = pd.ExcelWriter(output_path, engine="xlsxwriter")
            pbar.update(1)
            df.to_excel(writer, sheet_name="Audit", index=False)
            pbar.update(1)
            _apply_formatting(writer, df)
            writer.close()
            pbar.update(1)
    else:
        writer = pd.ExcelWriter(output_path, engine="xlsxwriter")
        df.to_excel(writer, sheet_name="Audit", index=False)
        _apply_formatting(writer, df)
        writer.close()

    logger.info("Report written: %s (%d rows)", output_path, len(rows))


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _apply_formatting(writer: pd.ExcelWriter, df: pd.DataFrame) -> None:
    """
    Apply header styling, conditional row fills, and column widths.

    All formatting is applied via xlsxwriter's range-level API rather
    than per-cell, keeping performance O(columns) not O(rows).
    """
    wb = writer.book
    ws = writer.sheets["Audit"]
    n_rows = len(df)
    n_cols = len(df.columns)

    # --- Header format ---
    header_fmt = wb.add_format({
        "bold": True,
        "font_color": _HEADER_FG,
        "bg_color": _HEADER_BG,
        "align": "center",
        "border": 0,
    })
    for col_idx, col_name in enumerate(df.columns):
        ws.write(0, col_idx, col_name, header_fmt)

    # --- Conditional formatting: green for True, yellow for False ---
    # Compute has_accommodation column letter dynamically from the spec.
    col_names_list = [c for c, _ in _COLUMN_SPEC]
    has_accom_idx = col_names_list.index("has_accommodation")
    has_accom_col_letter = _col_letter(has_accom_idx)
    data_range = f"A2:{_col_letter(n_cols - 1)}{n_rows + 1}"

    green_fmt = wb.add_format({"bg_color": _GREEN_HEX})
    yellow_fmt = wb.add_format({"bg_color": _YELLOW_HEX})

    if n_rows > 0:
        ws.conditional_format(data_range, {
            "type": "formula",
            "criteria": f"=${has_accom_col_letter}2=TRUE",
            "format": green_fmt,
        })
        ws.conditional_format(data_range, {
            "type": "formula",
            "criteria": f"=${has_accom_col_letter}2=FALSE",
            "format": yellow_fmt,
        })

    # --- Freeze header row ---
    ws.freeze_panes(1, 0)

    # --- Auto-fit column widths (sample first 500 rows for speed) ---
    for col_idx, col_name in enumerate(df.columns):
        col_data = df.iloc[:500, col_idx].astype(str)
        max_len = max(col_data.str.len().max(), len(col_name))
        max_len = max(10, min(int(max_len) + 2, 50))
        ws.set_column(col_idx, col_idx, max_len)


def _col_letter(zero_indexed: int) -> str:
    """Convert a 0-indexed column number to an Excel column letter."""
    result = ""
    n = zero_indexed
    while True:
        result = chr(ord("A") + n % 26) + result
        n = n // 26 - 1
        if n < 0:
            break
    return result
