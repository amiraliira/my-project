"""Bounded, read-only OOXML inventory reader; never evaluates Excel formulas."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from io import BytesIO
import posixpath
import re
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from .common import ApiError

MAX_UPLOAD = 8 * 1024 * 1024
MAX_ROWS = 20_000
MAX_COLUMNS = 128
_NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
_REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
DIGITS = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')
_FOREIGN_CURRENCY = re.compile(r'تومان|تومن|دلار|یورو|[$€£¥]|\b(?:toman|usd|eur|gbp|dollars?|euros?)\b', re.IGNORECASE)


@dataclass(frozen=True)
class Cell:
    value: str = ''
    kind: str = 's'
    formula: bool = False
    number_format: str = ''


def normalize_header(value):
    value = value.translate(DIGITS).replace('ي', 'ی').replace('ك', 'ک').casefold()
    return re.sub(r'[\s\W_]+', '', value, flags=re.UNICODE)


def unsupported_currency(value):
    return bool(_FOREIGN_CURRENCY.search(value))


_ALIASES = {
    'code': ('کد', 'کد کالا', 'کد قطعه', 'کد انبار', 'کد جنس', 'شناسه کالا', 'code', 'item code', 'part code', 'sku'),
    'name': ('عنوان', 'نام', 'نام کالا', 'عنوان کالا', 'نام قطعه', 'شرح کالا', 'شرح قطعه', 'شرح', 'name', 'item name', 'description'),
    'specification': ('مشخصه فنی', 'مشخصات فنی', 'مشخصات', 'مشخصه', 'specification', 'specifications'),
    'unit': ('واحد', 'واحد اندازه گیری', 'واحد شمارش', 'unit', 'uom'),
    'unitCost': ('قیمت', 'قیمت واحد', 'بهای واحد', 'نرخ', 'فی', 'فی واحد', 'قیمت ریال', 'قیمت واحد ریال', 'بهای واحد ریال', 'فی ریال', 'فی واحد ریال', 'نرخ ریال', 'unit price', 'unit cost', 'price', 'price irr', 'unit price irr', 'unit cost irr', 'price rial', 'unit price rial'),
}
_FIELDS = {normalize_header(alias): field for field, aliases in _ALIASES.items() for alias in aliases}


def _error(message='فایل اکسل معتبر نیست؛ یک فایل xlsx سالم و بدون رمز انتخاب کنید.'):
    return ApiError(400, message)


def _xml(archive, name):
    try:
        data = archive.read(name)
        if len(data) > 24 * 1024 * 1024:
            raise _error('حجم بازشدهٔ یکی از بخش‌های اکسل بیش از حد مجاز است.')
        value = data.decode('utf-16' if data.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig')
        if '<!DOCTYPE' in value.upper() or '<!ENTITY' in value.upper():
            raise _error('اکسل دارای تعریف XML خارجی یا نامعتبر است.')
        return ET.fromstring(value)
    except (KeyError, UnicodeError, ET.ParseError):
        raise _error() from None


def _column(reference):
    match = re.fullmatch(r'([A-Z]{1,3})([1-9][0-9]{0,6})', reference)
    if not match:
        raise _error('نشانی یکی از سلول‌های اکسل معتبر نیست.')
    number = 0
    for char in match[1]:
        number = number * 26 + ord(char) - 64
    return number, int(match[2])


def column_name(number):
    result = ''
    while number:
        number, rest = divmod(number - 1, 26)
        result = chr(65 + rest) + result
    return result


def _index(value):
    if not re.fullmatch(r'[0-9]{1,10}', value):
        raise _error('شمارهٔ متن یا قالب سلول اکسل معتبر نیست.')
    return int(value)


def _header(rows):
    candidates = []
    for number, cells in rows[:30]:
        found = {}
        for col, cell in cells.items():
            if cell.formula:
                continue
            raw = normalize_header(cell.value)
            without_currency = normalize_header(_FOREIGN_CURRENCY.sub('', cell.value))
            field = _FIELDS.get(raw) or (_FIELDS.get(without_currency) if raw != without_currency else None)
            if field:
                found.setdefault(field, []).append((col, cell.value))
        if 'code' in found and len(found) >= 2:
            candidates.append((len(found), number, found))
    if not candidates:
        return None
    # More recognizable columns wins; equal candidates require a visible choice/fix.
    candidates.sort(key=lambda item: (-item[0], item[1]))
    best = candidates[0]
    return {'row': best[1], 'fields': best[2], 'ambiguousRows': len([x for x in candidates if x[0] == best[0]]) > 1}


def read_workbook(content):
    if not isinstance(content, bytes) or not content or len(content) > MAX_UPLOAD:
        raise ApiError(413 if isinstance(content, bytes) and len(content) > MAX_UPLOAD else 400, 'فایل xlsx باید حداکثر ۸ مگابایت باشد.')
    try:
        with ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if len(entries) > 512 or len(names) != len(set(names)) or sum(x.file_size for x in entries) > 64 * 1024 * 1024:
                raise _error('ساختار یا حجم بازشدهٔ اکسل بیش از حد مجاز است.')
            for entry in entries:
                if (entry.flag_bits & 1 or entry.file_size > 24 * 1024 * 1024 or entry.filename.startswith('/')
                        or '\\' in entry.filename or '..' in entry.filename.split('/') or ':' in entry.filename):
                    raise _error()
            if any('vbaproject' in name.lower() or name.startswith('xl/externalLinks/') for name in names):
                raise _error('فایل دارای ماکرو یا اتصال خارجی قابل ورود نیست؛ مقادیر را در یک xlsx معمولی ذخیره کنید.')
            for name in names:
                if name.endswith('.rels'):
                    for rel in _xml(archive, name):
                        if rel.get('TargetMode', '').lower() == 'external':
                            raise _error('اکسل دارای پیوند خارجی است؛ یک نسخهٔ بدون اتصال خارجی بسازید.')
            workbook = _xml(archive, 'xl/workbook.xml')
            if workbook.tag != _NS + 'workbook':
                raise _error('این نوع فایل اکسل پشتیبانی نمی‌شود؛ با قالب Excel Workbook (.xlsx) ذخیره کنید.')
            relationships = {rel.get('Id'): rel.get('Target', '') for rel in _xml(archive, 'xl/_rels/workbook.xml.rels')}
            strings = []
            if 'xl/sharedStrings.xml' in names:
                for item in _xml(archive, 'xl/sharedStrings.xml').findall(_NS + 'si'):
                    strings.append(''.join(node.text or '' for node in item.iter(_NS + 't')))
                    if len(strings) > 200_000:
                        raise _error('تعداد متن‌های اکسل بیش از حد مجاز است.')
            formats = {}
            styles = []
            if 'xl/styles.xml' in names:
                style_xml = _xml(archive, 'xl/styles.xml')
                formats = {int(item.get('numFmtId')): item.get('formatCode', '') for item in style_xml.findall(_NS + 'numFmts/' + _NS + 'numFmt')}
                styles = [formats.get(int(item.get('numFmtId', '0')), 'General' if item.get('numFmtId', '0') == '0' else '0' if item.get('numFmtId') == '1' else 'unsupported') for item in style_xml.findall(_NS + 'cellXfs/' + _NS + 'xf')]
            result = []
            for sheet in workbook.findall(_NS + 'sheets/' + _NS + 'sheet'):
                target = relationships.get(sheet.get(_REL), '')
                path = posixpath.normpath(target.lstrip('/') if target.startswith('/') else posixpath.join('xl', target))
                if not path.startswith('xl/worksheets/') or path not in names:
                    raise _error('مسیر برگهٔ اکسل معتبر نیست.')
                rows, cell_count = [], 0
                previous_row = 0
                for row in _xml(archive, path).findall(_NS + 'sheetData/' + _NS + 'row'):
                    number = int(row.get('r', '0'))
                    if number <= previous_row or number > 1_048_576:
                        raise _error('شمارهٔ ردیف‌های اکسل معتبر نیست.')
                    previous_row = number
                    cells = {}
                    for item in row.findall(_NS + 'c'):
                        col, cell_row = _column(item.get('r', ''))
                        if col > MAX_COLUMNS or cell_row != number or col in cells:
                            raise _error('اکسل باید حداکثر ۱۲۸ ستون با سلول‌های یکتا داشته باشد.')
                        kind = item.get('t', 'n')
                        raw = item.findtext(_NS + 'v', '')
                        value = strings[_index(raw)] if kind == 's' else ''.join(node.text or '' for node in item.iter(_NS + 't')) if kind == 'inlineStr' else raw
                        style = _index(item.get('s', '0'))
                        if style and not styles:
                            raise _error('قالب ارجاع‌شدهٔ سلول اکسل وجود ندارد.')
                        cells[col] = Cell(value, kind, item.find(_NS + 'f') is not None, styles[style] if styles else 'General')
                        cell_count += 1
                    if cells:
                        rows.append((number, cells))
                    if len(rows) > MAX_ROWS + 30 or cell_count > 400_000:
                        raise _error('حداکثر ۲۰ هزار ردیف داده در هر برگه قابل ورود است.')
                result.append({'name': sheet.get('name', ''), 'visible': sheet.get('state', 'visible') == 'visible', 'rows': rows, 'header': _header(rows)})
            if not result or len(result) > 50 or len({s['name'] for s in result}) != len(result):
                raise _error('تعداد یا نام برگه‌های اکسل معتبر نیست.')
            return result
    except ApiError:
        raise
    except (BadZipFile, ValueError, IndexError, KeyError, OverflowError, NotImplementedError, RuntimeError):
        raise _error() from None


def cell_text(cell, field):
    if cell.formula or cell.kind in ('e', 'b', 'd'):
        raise _error('سلول فرمول، خطا، تاریخ یا مقدار بله/خیر دارد؛ مقدار نهایی را به‌صورت متن یا عدد وارد کنید.')
    value = cell.value.strip()
    if field == 'code':
        value = value.translate(DIGITS)
        if cell.kind == 'n' and value:
            try:
                if len(value) > 128:
                    raise ValueError
                number = Decimal(value)
                if not number.is_finite() or abs(number.as_tuple().exponent) > 100 or number < 0 or number > 999_999_999_999_999 or number != number.to_integral_value():
                    raise ValueError
                value = str(int(number))
                if re.fullmatch('0{1,100}', cell.number_format):
                    value = value.zfill(len(cell.number_format))
                elif cell.number_format not in ('General', '@'):
                    raise ValueError
            except (InvalidOperation, ValueError, OverflowError):
                raise _error('کد عددی مبهم است؛ ستون کد را با حفظ صفرهای اول به‌صورت Text ذخیره کنید.') from None
    return value


def price(cell):
    value = cell_text(cell, 'unitCost').translate(DIGITS).replace('٬', ',').replace('٫', '.')
    value = re.sub(r'\s*(?:ریال|IRR|rial)\s*$', '', value, flags=re.IGNORECASE).strip()
    if not value:
        return None
    if len(value) > 128:
        raise _error('قیمت از محدودهٔ مجاز بزرگ‌تر است.')
    if cell.kind == 'n':
        try:
            numeric = Decimal(value)
            if not numeric.is_finite() or abs(numeric.as_tuple().exponent) > 100 or numeric < 0 or numeric > 1_000_000_000_000 or numeric != numeric.quantize(Decimal('0.01')):
                raise InvalidOperation
            return numeric
        except InvalidOperation:
            raise _error('قیمت باید عدد ریالی نامنفی، حداکثر یک تریلیون و با حداکثر دو رقم اعشار باشد.') from None
    if not re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]{1,2})?', value):
        raise _error('قیمت باید عدد ریالی نامنفی با حداکثر دو رقم اعشار باشد؛ جداکنندهٔ هزارگان را درست وارد کنید.')
    cost = Decimal(value.replace(',', ''))
    if cost > 1_000_000_000_000:
        raise _error('بهای واحد از سقف یک تریلیون ریال بیشتر است.')
    return cost
