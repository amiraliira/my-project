"""Bounded CSV exports, formula escaping, explicit stream resource cleanup."""
import csv
from io import StringIO
import threading
import time
import tempfile
from pathlib import Path
from .api import Result
from .common import ApiError
from .quantity import value
from .pm_calendar import format_persian
from .permissions import can_view_personnel_reports

_slots = threading.BoundedSemaphore(4)
EXPORT_MAX_BYTES = 128 * 1024 * 1024
EXPORT_MEMORY_BYTES = 1024 * 1024


def csv_line(values):
    output = StringIO(newline="")
    # Only text can be a spreadsheet formula. Numbers retain their numeric form.
    def cell(value):
        if value is None:
            return ""
        if isinstance(value, str) and value.lstrip(" \t\r\n\ufeff").startswith(("=", "+", "-", "@")):
            return "'" + value
        return value
    csv.writer(output, lineterminator="\r\n").writerow(cell(v) for v in values)
    return output.getvalue().encode("utf-8")


class ExportStream:
    def __init__(self, headings, rows, *, deadline_seconds=600, clock=time.monotonic):
        self._lock, self._closed = threading.RLock(), True
        self.rows, self._prepared = None, None
        if not _slots.acquire(blocking=False):
            raise ApiError(429, "چهار خروجی دیگر در حال تهیه است؛ کمی بعد دوباره تلاش کنید.")
        self._closed = False
        try:
            self.rows, self._clock = iter(rows), clock
            self._deadline_seconds = deadline_seconds
            self._deadline = clock() + deadline_seconds
            self._prefix = iter((b"\xef\xbb\xbf", csv_line(headings)))
        except BaseException:
            self.close()
            raise

    def prepare(self, directory, *, max_bytes=EXPORT_MAX_BYTES):
        """Finish one consistent database snapshot before starting a download.

        Slow clients then read a bounded private temporary file, rather than
        keeping a transaction idle across network yields or delaying vacuum.
        The 4-export limit also bounds prepared downloads. Temporary data is
        deleted on completion, disconnect, error or abandoned object cleanup.
        """
        from .network_policy import validate_file_path
        if type(max_bytes) is not int or not 1 <= max_bytes <= EXPORT_MAX_BYTES:
            raise ValueError('Invalid export byte bound')
        temporary = None
        with self._lock:
            if self._closed or self._prepared is not None:
                raise ValueError('Export is already closed or prepared')
            try:
                directory = Path(validate_file_path(str(directory)))
                if str(directory).startswith('\\\\'):
                    raise ValueError('Export temporary files require local disk')
                # Inherit the explicitly configured local backup directory's
                # access policy, including the Windows service account ACL.
                directory.mkdir(parents=True, exist_ok=True)
                validate_file_path(str(directory))
                temporary = tempfile.SpooledTemporaryFile(max_size=EXPORT_MEMORY_BYTES,
                    mode='w+b', dir=directory, prefix='pak-export-')
                size = 0
                def append(chunk):
                    nonlocal size
                    if self._clock() >= self._deadline:
                        raise ApiError(503, 'تهیهٔ گزارش طول کشید؛ جست‌وجو یا نوع کار را محدودتر کنید و دوباره تلاش کنید.')
                    size += len(chunk)
                    if size > max_bytes:
                        raise ApiError(413, 'حجم خروجی بیش از ۱۲۸ مگابایت است؛ جست‌وجو یا نوع کار را محدودتر کنید.')
                    temporary.write(chunk)
                for prefix in self._prefix:
                    append(prefix)
                for row in self.rows:
                    append(csv_line(row))
                close = getattr(self.rows, 'close', None)
                if close:
                    close()
                temporary.seek(0)
                self._prepared, temporary = temporary, None
                # Preparing a large snapshot must not consume its download time.
                self._deadline = self._clock() + self._deadline_seconds
                return self
            except BaseException:
                self.close()
                raise
            finally:
                if temporary is not None:
                    temporary.close()

    def __iter__(self):
        return self

    def __next__(self):
        with self._lock:
            if self._closed:
                raise StopIteration
            try:
                if self._clock() >= self._deadline:
                    raise TimeoutError("CSV export deadline exceeded")
                if self._prepared is not None:
                    chunk = self._prepared.read(64 * 1024)
                    if not chunk:
                        raise StopIteration
                    return chunk
                prefix = next(self._prefix, None)
                return prefix if prefix is not None else csv_line(next(self.rows))
            except BaseException:
                self.close()
                raise

    def close(self):
        with self._lock:
            if not self._closed:
                self._closed = True
                try:
                    close = getattr(self.rows, "close", None)
                    if close:
                        close()
                finally:
                    try:
                        if self._prepared is not None:
                            self._prepared.close()
                    finally:
                        _slots.release()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


def report(services, request, user, name):
    if name == "people" and not can_view_personnel_reports(user):
        raise ApiError(403, "گزارش عملکرد نفرات فقط در اختیار رئیس است.")
    if request.method == "HEAD":
        return Result(status=204)
    q = request.query
    if name == "works":
        headings = ("شماره", "تاریخ میلادی", "تاریخ شمسی", "نوع", "وضعیت", "کد دستگاه", "دستگاه", "عنوان", "شرح", "مدت تعمیر", "توقف تولید", "امتیاز نهایی")
        def rows():
            from datetime import date
            for w in services.work.export(user, type=q.get('type'), status=q.get('status'), q=q.get('q'), asset_id=q.get('assetId')):
                jalali = format_persian(date.fromisoformat(w["workDate"])) if w.get("workDate") else ""
                yield [w.get(k) for k in ("number", "workDate")] + [jalali] + [w.get(k) for k in ("type", "status", "assetCode", "assetName", "title", "description", "durationMinutes", "downtimeMinutes", "score")]
    elif name == "people":
        headings = ("شماره کار", "تاریخ", "پرسنل", "تخصص", "دقیقه", "ضریب سرپرست", "ضریب رئیس", "امتیاز")
        def rows():
            for w in services.work.export(user, status="completed"):
                for p in w["participants"]:
                    if user.role == "supervisor" and p["specialty"] != user.specialty:
                        continue
                    yield (w["number"], w["workDate"], p["personName"], p["specialty"], p["minutes"], p.get("supervisorCoefficient"), p.get("headCoefficient"), p.get("finalScore"))
    elif name == "consumption-details":
        headings = ("کد انبار", "نام قطعه", "واحد", "مقدار مصرف تأییدشده", "شماره گزارش", "تاریخ انجام", "تاریخ تأیید نهایی", "تأییدکننده")
        def rows():
            for p in services.consumption.export(user, q.get("q"), q.get("partId"), q.get("from"), q.get("to")):
                yield [p.get(k) for k in ("partCode", "partName", "unit", "quantity", "orderNumber", "executedAt", "approvedAt", "actorName")]
    elif name in ("stock", "consumption"):
        headings = ("کد انبار", "نام قطعه", "مشخصات فنی", "واحد", "مجموع مصرف تأییدشده", "تعداد گزارش نهایی")
        def rows():
            with services.db.transaction(write=False) as c:
                after = None
                while True:
                    batch = c.query("SELECT p.id,p.code,p.name,p.specification,p.unit,COALESCE(t.quantity_units,0) AS units,COALESCE(t.report_count,0) AS reports FROM parts p LEFT JOIN part_consumption_totals t ON t.part_id=p.id WHERE (CAST(:after AS TEXT) IS NULL OR p.id>:after) ORDER BY p.id LIMIT 100", {"after": after})
                    if not batch:
                        return
                    for p in batch:
                        yield (p["code"], p["name"], p["specification"], p["unit"], value(p["units"]), p["reports"])
                    after = batch[-1]["id"]
    else:
        raise ApiError(404, "گزارش پیدا نشد.")
    stream = ExportStream(headings, rows()).prepare(services.backup.directory)
    return Result(stream=stream, media_type="text/csv; charset=utf-8",
                  headers=(("content-disposition", f'attachment; filename="pak-{name}.csv"'),))
