"""Excel export for the Reports page: tidy table plus a native chart per metric.

The workbook is generated with openpyxl so the chart is a real Excel chart object
(not an image), it references the data cells on the Report sheet, and the numbers
stay numbers: an operator can re-point the series, extend the range, or pivot the
table without retyping anything.

Every value here comes from the same history builder the UI uses, so the file and
the screen can never disagree.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Any, Iterable

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

# Palette literals: an .xlsx cannot read CSS custom properties, so the exported
# file carries the same ink/linen values the interface uses.
INK = "FF1E1E1E"
LINEN = "FFF4EFE4"
MUTED = "FF5E5A52"
JAKARTA = timezone(timedelta(hours=7))

# (metric key, column header, unit label, Excel number format)
MetricSpec = tuple[str, str, str, str]


def jakarta_label(ms: int) -> str:
    """Timestamps in the file carry the site's timezone, like the interface does."""
    return datetime.fromtimestamp(int(ms) / 1000, tz=JAKARTA).strftime("%Y-%m-%d %H:%M:%S")


def build_report_workbook(
    *,
    device_id: str,
    site_label: str,
    window_label: str,
    generated_ms: int,
    metrics: Iterable[MetricSpec],
    rows: list[dict[str, Any]],
) -> bytes:
    """Return an .xlsx with one tidy data sheet.

    No chart sheet: the export is the data artifact (numbers stay numbers, filters
    and frozen headers intact), and the visual reading of the same series belongs
    to the Reports page and to the browser's print/PDF view.
    """
    specs = list(metrics)
    wb = Workbook()
    ws = wb.active
    ws.title = "Report"
    ws.sheet_view.showGridLines = False

    title_font = Font(bold=True, size=14, color=INK)
    meta_font = Font(size=10, color=MUTED)
    head_font = Font(bold=True, size=10, color="FFFFFFFF")
    head_fill = PatternFill("solid", start_color=INK)
    band_fill = PatternFill("solid", start_color=LINEN)

    ws["A1"] = "TELNUSA NMS - telemetry report"
    ws["A1"].font = title_font
    for offset, (label, value) in enumerate((
        ("Device", device_id),
        ("Site", site_label),
        ("Window", window_label),
        ("Generated (Asia/Jakarta)", jakarta_label(generated_ms)),
        ("Samples", str(len(rows))),
    )):
        ws.cell(row=2 + offset, column=1, value=label).font = meta_font
        cell = ws.cell(row=2 + offset, column=2, value=value)
        cell.font = meta_font

    header_row = 9
    headers = ["Timestamp (Asia/Jakarta)"] + [f"{name} [{unit}]" for _, name, unit, _ in specs]
    for col, text in enumerate(headers, start=1):
        cell = ws.cell(row=header_row, column=col, value=text)
        cell.font = head_font
        cell.fill = head_fill
        cell.alignment = Alignment(horizontal="left", vertical="center")

    for index, row in enumerate(rows):
        excel_row = header_row + 1 + index
        stamp = ws.cell(row=excel_row, column=1, value=jakarta_label(row["ts"]))
        stamp.alignment = Alignment(horizontal="left")
        if index % 2:
            stamp.fill = band_fill
        for col, (key, _, _, number_format) in enumerate(specs, start=2):
            value = row.get(key)
            cell = ws.cell(row=excel_row, column=col,
                           value=float(value) if isinstance(value, (int, float)) else None)
            cell.number_format = number_format
            if index % 2:
                cell.fill = band_fill

    ws.freeze_panes = ws.cell(row=header_row + 1, column=1)
    ws.column_dimensions["A"].width = 22
    for col in range(2, len(specs) + 2):
        ws.column_dimensions[get_column_letter(col)].width = 16
    if rows:
        ws.auto_filter.ref = (f"A{header_row}:"
                              f"{get_column_letter(len(specs) + 1)}{header_row + len(rows)}")

    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
