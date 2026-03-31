"""
Excel report generation for accommodation audit results.

Uses pandas + xlsxwriter for fast bulk writes. At 57k rows, openpyxl
(pure Python, per-cell) is orders of magnitude slower than xlsxwriter
(C extension, streaming write). Conditional formatting is applied as a
range-level rule rather than per-cell, which is both faster and idiomatic
for xlsxwriter.

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
# Colours (xlsxwriter hex strings, no leading #)
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
    ("course_id",               lambda r: r.course_id),
    ("quiz_id",                 lambda r: r.quiz_id),
    ("engine",                  lambda r: r.engine),
    ("accommodation_type",      lambda r: r.accommodation_type.value if r.accommodation_type else None),
    ("user_id",                 lambda r: r.user_id),
    ("item_id",                 lambda r: r.item_id),
    ("has_accommodation",       lambda r: r.has_accommodation),
    ("completed",               lambda r: r.completed),
    # Details columns — empty when the accommodation type doesn't produce that field.
    ("extra_time",              lambda r: r.details.get("extra_time")),
    ("extra_time_in_seconds",   lambda r: r.details.get("extra_time_in_seconds")),
    ("timer_multiplier_value",  lambda r: r.details.get("timer_multiplier_value")),
    ("extra_attempts",          lambda r: r.details.get("extra_attempts")),
    ("spell_check",             lambda r: r.details.get("spell_check")),
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

    Uses pandas + xlsxwriter for fast bulk writes. Conditional formatting
    is applied as a range rule (not per-cell) so performance scales well
    even at tens of thousands of rows.

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
    than per-cell, which keeps performance O(columns) not O(rows).
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
    # has_accommodation is the 7th column (index 6, Excel column G).
    # We apply a range rule over the data rows that checks column G.
    has_accom_col_letter = _col_letter(6)  # 0-indexed → "G"
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

    # --- Auto-fit column widths (approximate) ---
    for col_idx, col_name in enumerate(df.columns):
        # Sample up to 500 rows to estimate column width.
        col_data = df.iloc[:500, col_idx].astype(str)
        max_len = max(col_data.str.len().max(), len(col_name))
        max_len = max(10, min(int(max_len) + 2, 50))
        ws.set_column(col_idx, col_idx, max_len)


def _col_letter(zero_indexed: int) -> str:
    """Convert a 0-indexed column number to an Excel column letter (A, B, ... Z, AA...)."""
    result = ""
    n = zero_indexed
    while True:
        result = chr(ord("A") + n % 26) + result
        n = n // 26 - 1
        if n < 0:
            break
    return result
