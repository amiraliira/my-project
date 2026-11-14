"""Download the published working PM revision as a self-contained Persian PDF.

No source-original file is rewritten. Table anchors come from the same working
geometry as the in-app editor; text is wrapped logically before Arabic shaping.
"""
from io import BytesIO
from pathlib import Path
from threading import Lock
import re

from .common import ApiError

_FONT_LOCK = Lock()
_FONT = "PAK-DejaVuSans"


def _dependencies():
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.pdfgen.canvas import Canvas
        from arabic_reshaper import ArabicReshaper
        from bidi.algorithm import get_display
    except ImportError:
        raise ApiError(503, "وابستگی‌های خروجی PDF نصب نیست؛ فایل requirements.txt نسخهٔ جدید نصب شود.") from None
    with _FONT_LOCK:
        if _FONT not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(_FONT, str(Path(__file__).with_name("DejaVuSans.ttf"))))
    reshaper = ArabicReshaper(configuration={"delete_harakat": False, "shift_harakat_position": True})
    return pdfmetrics, Canvas, lambda value: get_display(reshaper.reshape(value), base_dir="R")


def render_template_pdf(template):
    from .pm_activity_structure import _grid
    metrics, Canvas, visual = _dependencies()
    tables = template.get("tables", [])
    grids = {table["id"]: _grid(table) for table in tables}
    columns = max((max((e["c"] + e["w"] for e in grid), default=1) for grid in grids.values()), default=1)
    # Wide group matrices use a landscape page, rather than clipping columns.
    width, height = (841.89, 595.28) if columns > 8 else (595.28, 841.89)
    margin, bottom = 30, 35
    usable = width - 2 * margin
    output = BytesIO()
    canvas = Canvas(output, pagesize=(width, height), pageCompression=1, invariant=1)
    canvas.setTitle(str(template.get("title", "PM")))
    canvas.setAuthor("PAK Maintenance")
    canvas.setSubject("Published working PM template revision " + str(template.get("version", "")))
    page, y = 0, 0

    def lines(value, available, size):
        value = str(value or "").replace("\x00", "")
        result = []
        for paragraph in value.splitlines() or [""]:
            line = ""
            for word in paragraph.split():
                candidate = (line + " " + word).strip()
                if metrics.stringWidth(visual(candidate), _FONT, size) <= available:
                    line = candidate
                    continue
                if line:
                    result.append(visual(line))
                line = ""
                for char in word:
                    if line and metrics.stringWidth(visual(line + char), _FONT, size) > available:
                        result.append(visual(line)); line = ""
                    line += char
            result.append(visual(line))
        return result

    def text(value, x, top, available, size=9, leading=None):
        leading = leading or size * 1.5
        prepared = lines(value, available, size)
        canvas.setFont(_FONT, size)
        for index, line in enumerate(prepared):
            canvas.drawRightString(x + available, top - size - index * leading, line)
        return len(prepared) * leading

    def new_page():
        nonlocal y, page
        if page:
            canvas.showPage()
        page += 1
        canvas.setFillColorRGB(.10, .19, .23)
        title = template.get("title") or "برگهٔ بازدید PM"
        used = text(title, margin, height - margin, usable, 12, 17)
        y = height - margin - used - 6
        meta = " | ".join(str(template.get(key) or "") for key in ("hall", "specialty", "period"))
        meta += " | نسخهٔ " + str(template.get("version", ""))
        y -= text(meta, margin, y, usable, 8) + 12
        canvas.setStrokeColorRGB(.65, .71, .74)
        canvas.line(margin, y + 5, width - margin, y + 5)
        canvas.setFillColorRGB(.35, .39, .42)
        text("نسخهٔ جاری سامانه - صفحهٔ " + str(page), margin, 25, usable, 7)
        canvas.setFillColorRGB(.10, .13, .16)

    def paragraph(value):
        nonlocal y
        for line in lines(value, usable, 9):
            if y - 14 < bottom:
                new_page()
            canvas.setFont(_FONT, 9)
            canvas.drawRightString(width - margin, y - 9, line)
            y -= 14
        y -= 5

    def table_view(table):
        nonlocal y
        grid = grids[table["id"]]
        if not grid:
            return
        cols = max(e["c"] + e["w"] for e in grid)
        rows = max(e["r"] + e["h"] for e in grid)
        if cols > 100 or rows > 1500:
            raise ApiError(409, "جدول برگه برای خروجی PDF باید بررسی شود.")
        unit = usable / cols
        size = 8 if cols > 8 else 9
        prepared = {}
        heights = [22.0] * rows
        for e in sorted(grid, key=lambda item: item["h"]):
            wrapped = lines(e["cell"].get("text", ""), unit * e["w"] - 8, size)
            prepared[id(e)] = wrapped
            required = max(22, len(wrapped) * size * 1.5 + 9)
            present = sum(heights[e["r"]:e["r"] + e["h"]])
            if present < required:
                extra = (required - present) / e["h"]
                for row in range(e["r"], e["r"] + e["h"]):
                    heights[row] += extra
        # A page break never cuts a merged cell. Extremely tall merged cells
        # fail visibly; no silent loss, shrinking to unreadable text or clipping.
        safe = [r for r in range(1, rows + 1) if not any(e["r"] < r < e["r"] + e["h"] for e in grid)]
        start = 0
        if y - 40 < bottom:
            new_page()
        while start < rows:
            candidates = [r for r in safe if r > start and sum(heights[start:r]) <= y - bottom]
            if not candidates:
                new_page()
                candidates = [r for r in safe if r > start and sum(heights[start:r]) <= y - bottom]
            if not candidates:
                raise ApiError(409, "ارتفاع یک خانهٔ ادغام‌شده از صفحه بیشتر است؛ متن یا ساختار برگه را کوتاه کنید.")
            end = max(candidates)
            offsets = {start: 0.0}
            for row in range(start, end):
                offsets[row + 1] = offsets[row] + heights[row]
            for e in grid:
                if not start <= e["r"] < end:
                    continue
                span_width, span_height = unit * e["w"], offsets[e["r"] + e["h"]] - offsets[e["r"]]
                column = cols - e["c"] - e["w"] if table.get("direction") == "rtl" else e["c"]
                x, top = margin + unit * column, y - offsets[e["r"]]
                active = e["cell"].get("activity") is True
                canvas.setFillColorRGB(*((.93, .97, .96) if active else (1, 1, 1)))
                canvas.setStrokeColorRGB(.55, .61, .64)
                canvas.setLineWidth(.4)
                canvas.rect(x, top - span_height, span_width, span_height, fill=1, stroke=1)
                canvas.setFillColorRGB(.08, .12, .14)
                canvas.setFont(_FONT, size)
                for index, line in enumerate(prepared[id(e)]):
                    canvas.drawRightString(x + span_width - 4, top - 5 - size - index * size * 1.5, line)
            y -= offsets[end] + 10
            start = end
            if start < rows:
                new_page()

    new_page()
    emitted = set()
    for block in template.get("bodyBlocks", []):
        if block.get("type") == "table":
            identifier = block.get("tableId")
            table = next((t for t in tables if t["id"] == identifier), None)
            if table is not None and identifier not in emitted:
                table_view(table); emitted.add(identifier)
        elif isinstance(block.get("text"), str) and block["text"].strip():
            paragraph(block["text"])
    for table in tables:
        if table["id"] not in emitted:
            table_view(table)
    canvas.save()
    return output.getvalue()
