"""Versioned activity edits, retaining imported table geometry and source evidence.

Only activity units can be cloned/deleted. Arbitrary client table JSON is never
trusted. An imported equipment form uses rows; a grouped equipment matrix uses
columns, including its result cells. Source references remain evidence; explicit
working coordinates describe the revised layout instead of falsifying them.
"""
from copy import deepcopy
import hashlib
import re

from .common import ApiError, text


def _identifier(*values):
    return "work-" + hashlib.sha256("\n".join(map(str, values)).encode()).hexdigest()[:32]


def _column(value):
    result = 0
    for char in value.upper():
        result = result * 26 + ord(char) - 64
    return result


def _grid(table):
    """Return independent rectangular anchors; covered source cells are evidence."""
    rows = table.get("rows", [])
    if table.get("layoutVersion") == 1:
        return [dict(cell=deepcopy(cell), r=cell["layoutRow"] - 1,
                     c=cell["layoutColumn"] - 1, h=cell.get("rowSpan", 1),
                     w=cell.get("colSpan", 1))
                for row in rows for cell in row.get("cells", [])]
    source_rows = {row.get("sourceRow", i + 1): i for i, row in enumerate(rows)}
    ranges = []
    for value in table.get("originalMergedRanges", []):
        match = re.fullmatch(r"\$?([A-Z]+)\$?(\d+):\$?([A-Z]+)\$?(\d+)", value, re.I)
        if not match:
            raise ApiError(409, "محدودهٔ ادغام خانه‌های برگه معتبر نیست؛ اصل برگه بررسی شود.")
        a, b, c, d = match.groups()
        if int(b) not in source_rows or int(d) not in source_rows:
            raise ApiError(409, "سطر محدودهٔ ادغام در برگه پیدا نشد.")
        ranges.append((source_rows[int(b)], _column(a) - 1,
                       source_rows[int(d)] - source_rows[int(b)] + 1,
                       _column(c) - _column(a) + 1))
    positioned = []
    for r, row in enumerate(rows):
        column = 0
        for cell in row.get("cells", []):
            c = cell.get("sourceRef", {}).get("column", column + 1) - 1
            w = cell.get("colSpan", 1)
            positioned.append(dict(cell=deepcopy(cell), r=r, c=c, h=1, w=w))
            column = c + w
    result = []
    for entry in positioned:
        r, c, cell = entry["r"], entry["c"], entry["cell"]
        covered = next((region for region in ranges
                        if region[0] <= r < region[0] + region[2]
                        and region[1] <= c < region[1] + region[3]), None)
        if covered:
            if (r, c) != covered[:2]:
                continue
            entry.update(h=covered[2], w=covered[3])
        elif cell.get("originalVerticalMerge") == "continue":
            continue
        elif cell.get("originalVerticalMerge") == "restart":
            h = 1
            while any(other["r"] == r + h and other["c"] == c
                      and other["w"] == entry["w"]
                      and other["cell"].get("originalVerticalMerge") == "continue"
                      for other in positioned):
                h += 1
            entry["h"] = h
        result.append(entry)
    _validate_grid(result)
    return result


def _validate_grid(rects):
    occupied = set()
    for entry in rects:
        if min(entry["r"], entry["c"]) < 0 or not 1 <= entry["h"] <= 1000 or not 1 <= entry["w"] <= 300:
            raise ApiError(409, "ابعاد جدول برگه معتبر نیست.")
        for r in range(entry["r"], entry["r"] + entry["h"]):
            for c in range(entry["c"], entry["c"] + entry["w"]):
                if (r, c) in occupied:
                    raise ApiError(409, "خانه‌های جدول برگه روی هم افتاده‌اند؛ اصل برگه بررسی شود.")
                occupied.add((r, c))
    if len(occupied) > 100_000:
        raise ApiError(413, "جدول برگه بیش از حد بزرگ است.")


def _orientation(template, table, rects):
    active = [entry for entry in rects if entry["cell"].get("activity") is True]
    by_row = {}
    for entry in active:
        by_row[entry["r"]] = by_row.get(entry["r"], 0) + 1
    column = max(by_row.values(), default=0) > 1 or (
        template.get("formKind") == "grouped-inspection" and bool(table.get("sourceSheet")))
    if column and len(by_row) > 1:
        raise ApiError(409, "فعالیت‌های چندبخشی این جدول ابتدا باید بازبینی شوند.")
    return "column" if column else "row"


def describe_activity_structure(template):
    activities = []
    for table in template.get("tables", []):
        active = [cell for row in table.get("rows", []) for cell in row.get("cells", [])
                  if cell.get("activity") is True]
        if not active:
            continue
        rects = _grid(table)
        orientation = _orientation(template, table, rects)
        for entry in rects:
            cell = entry["cell"]
            if cell.get("activity") is True:
                activities.append(dict(id=cell["id"], text=cell.get("text", ""),
                                       tableId=table["id"], orientation=orientation))
    return activities


def _remove(rects, axis, start, size):
    dimension = "h" if axis == "r" else "w"
    end = start + size
    result = []
    for entry in rects:
        left, right = entry[axis], entry[axis] + entry[dimension]
        overlap = max(0, min(right, end) - max(left, start))
        if overlap == entry[dimension]:
            continue
        entry[axis] -= min(size, max(0, left - start))
        entry[dimension] -= overlap
        result.append(entry)
    return result


def _insert(rects, axis, position, size, exemplars, original_start, key, activity_id, value):
    dimension = "h" if axis == "r" else "w"
    far_edge = max(entry[axis] + entry[dimension] for entry in rects)
    for entry in rects:
        if entry[axis] >= position:
            entry[axis] += size
        elif (entry[axis] + entry[dimension] > position or
              entry[axis] + entry[dimension] == position == far_edge and entry[dimension] > size):
            entry[dimension] += size
    for old in exemplars:
        entry = deepcopy(old)
        entry[axis] = position + old[axis] - original_start
        cell = entry["cell"]
        cell["id"] = _identifier(key, cell["id"])
        cell["derivedFromActivityId"] = activity_id
        if cell.get("activity"):
            cell["text"] = value
        elif cell.get("editable"):
            cell["text"] = ""
        # It is a new working cell, not an untouched paragraph from the source.
        cell.pop("originalCellParagraphs", None)
        cell.pop("originalCellText", None)
        rects.append(entry)


def apply_activity_structure(template, desired):
    """Return a revised copy. Existing IDs retain order; new items clone an anchor."""
    if not isinstance(desired, list) or not 1 <= len(desired) <= 200:
        raise ApiError(400, "بین ۱ تا ۲۰۰ فعالیت وارد کنید.")
    before = describe_activity_structure(template)
    existing = {item["id"]: item for item in before}
    entries, seen = [], set()
    for index, raw in enumerate(desired):
        if not isinstance(raw, dict):
            raise ApiError(400, "فهرست فعالیت‌ها معتبر نیست.")
        identifier = text(raw.get("id") or "", maximum=120, field="شناسه فعالیت")
        value = text(raw.get("text"), maximum=10000, field="شرح فعالیت")
        clone = text(raw.get("cloneFromId") or "", maximum=120, field="فعالیت الگو")
        if not value or identifier and (identifier not in existing or identifier in seen):
            raise ApiError(400, "شرح فعالیت خالی یا شناسهٔ فعالیت نامعتبر/تکراری است.")
        if not identifier and clone not in existing:
            raise ApiError(400, "برای فعالیت جدید، فعالیت الگوی همین برگه را انتخاب کنید.")
        if identifier:
            seen.add(identifier)
        anchor = existing[identifier or clone]
        entries.append(dict(id=identifier, text=value, cloneFromId=clone,
                            tableId=anchor["tableId"], key=_identifier(template.get("id"), template.get("version"), index, value)))
    kept_before = [item["id"] for item in before if item["id"] in seen]
    if [entry["id"] for entry in entries if entry["id"]] != kept_before:
        raise ApiError(400, "ترتیب فعالیت‌های موجود را حفظ کنید؛ فعالیت تازه را کنار فعالیت الگو اضافه کنید.")
    table_order = [table["id"] for table in template.get("tables", [])]
    ranks = [table_order.index(entry["tableId"]) for entry in entries]
    if ranks != sorted(ranks):
        raise ApiError(400, "فعالیت‌های هر جدول باید در همان بخش برگه بمانند.")
    result = deepcopy(template)
    for table in result.get("tables", []):
        old_active = [item for item in before if item["tableId"] == table["id"]]
        if not old_active:
            continue
        selected = [entry for entry in entries if entry["tableId"] == table["id"]]
        if not selected:
            raise ApiError(400, "حداقل یک فعالیت در هر جدول فعالیت باقی بگذارید.")
        rects = _grid(table)
        orientation = _orientation(template, table, rects)
        axis, dimension = ("c", "w") if orientation == "column" else ("r", "h")
        originals = {entry["cell"]["id"]: deepcopy(entry) for entry in rects if entry["cell"].get("activity") is True}
        exemplars = {}
        for identifier, anchor in originals.items():
            exemplars[identifier] = [deepcopy(entry) for entry in rects
                                    if entry[axis] >= anchor[axis]
                                    and entry[axis] + entry[dimension] <= anchor[axis] + anchor[dimension]]
        for identifier, anchor in sorted(originals.items(), key=lambda pair: pair[1][axis], reverse=True):
            if identifier not in seen:
                rects = _remove(rects, axis, anchor[axis], anchor[dimension])
        previous = None
        for entry in selected:
            if entry["id"]:
                current = next(rect for rect in rects if rect["cell"]["id"] == entry["id"])
                current["cell"]["text"] = entry["text"]
            else:
                anchor_id = entry["cloneFromId"]
                anchor = originals[anchor_id]
                if previous is not None:
                    position = previous[axis] + previous[dimension]
                else:
                    next_existing = next((item["id"] for item in selected if item["id"]), None)
                    position = next(rect[axis] for rect in rects if rect["cell"]["id"] == next_existing) if next_existing else min(a[axis] for a in originals.values())
                _insert(rects, axis, position, anchor[dimension], exemplars[anchor_id], anchor[axis],
                        entry["key"], anchor_id, entry["text"])
                current = next(rect for rect in rects if rect["cell"]["id"] == _identifier(entry["key"], anchor_id))
            previous = current
        _validate_grid(rects)
        max_row = max(entry["r"] + entry["h"] for entry in rects)
        previous_rows = table.get("rows", [])
        new_rows = []
        for i in range(max_row):
            metadata = deepcopy(previous_rows[i]) if orientation == "column" and i < len(previous_rows) else {}
            metadata.update(id=_identifier(table["id"], "layout-row", i), cells=[])
            new_rows.append(metadata)
        number_columns = {entry["c"] for entry in rects if str(entry["cell"].get("text", "")).replace(" ", "") in ("ردیف", "شمارهردیف")}
        if orientation == "row":
            activity_rows = sorted({entry["r"] for entry in rects if entry["cell"].get("activity")})
            for number, row in enumerate(activity_rows, 1):
                for entry in rects:
                    cell = entry["cell"]
                    if entry["r"] == row and entry["c"] in number_columns and not cell.get("activity") and not cell.get("editable"):
                        cell["text"] = str(number)
        for entry in sorted(rects, key=lambda item: (item["r"], item["c"])):
            cell = entry["cell"]
            cell.update(layoutRow=entry["r"] + 1, layoutColumn=entry["c"] + 1,
                        rowSpan=entry["h"], colSpan=entry["w"])
            new_rows[entry["r"]]["cells"].append(cell)
        table.update(rows=new_rows, layoutVersion=1)
    result["activityStructureVersion"] = 1
    return result
