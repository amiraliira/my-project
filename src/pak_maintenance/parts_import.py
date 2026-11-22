"""Preview an ordinary inventory XLSX and atomically apply reviewed master data.

The fingerprint is an optimistic concurrency checksum, never an authorization
credential. Apply re-parses the file and rechecks account authority and every
matched part in the same transaction. No temporary database records or files.
"""
from __future__ import annotations

import hashlib
import re
from uuid import uuid4

from .common import ApiError, audit, decimal_value, dumps, text
from .master_data import MasterDataService, _actor, _EDIT_ROLES, _MAX_VERSION
from .parts_xlsx import MAX_UPLOAD, MAX_ROWS, Cell, cell_text, column_name, price, read_workbook, unsupported_currency
from .security import assert_live_actor, require

MAX_UPLOAD_BYTES = MAX_UPLOAD
_LIMITS = {'code': 100, 'name': 500, 'specification': 2000, 'unit': 100}
_DB_FIELDS = 'id,code,name,specification,unit,unit_cost,version'


class PartsImportService:
    def __init__(self, db):
        self.db = db

    def preview(self, user, content, sheet=None):
        require(user, *_EDIT_ROLES)
        workbook = read_workbook(content)
        with self.db.transaction(write=False) as c:
            assert_live_actor(c, user, lock=False)
            result, _ = self._plan(c, workbook, content, sheet)
            return result

    def apply(self, user, content, fingerprint, sheet=None):
        require(user, *_EDIT_ROLES)
        if not isinstance(fingerprint, str) or not re.fullmatch('[0-9a-f]{64}', fingerprint):
            raise ApiError(400, 'ابتدا پیش‌نمایش فایل اکسل را دریافت و بررسی کنید.')
        workbook = read_workbook(content)

        def operation(c):
            _actor(c, user, _EDIT_ROLES)
            result, plan = self._plan(c, workbook, content, sheet, lock=True)
            if result['fingerprint'] != fingerprint:
                raise ApiError(409, 'فایل یا اطلاعات قطعات پس از پیش‌نمایش تغییر کرده است؛ دوباره پیش‌نمایش بگیرید.')
            if not result['canApply']:
                raise ApiError(400, 'فایل تغییر قابل ثبت ندارد یا ردیف‌های آن نیاز به اصلاح دارند.')
            digest = hashlib.sha256(content).hexdigest()
            for item in plan:
                if item['status'] not in ('new', 'updated'):
                    continue
                values, old = item['values'], item['old']
                identifier = old['id'] if old else str(uuid4())
                previous = old['version'] if old else 0
                args = dict(values, id=identifier, previous=previous, version=previous + 1, cost=values['unitCost'])
                count = c.execute('INSERT INTO parts(id,code,name,specification,unit,unit_cost,version) VALUES(:id,:code,:name,:specification,:unit,:cost,:version)' if old is None else
                                  'UPDATE parts SET name=:name,specification=:specification,unit=:unit,unit_cost=:cost,version=:version WHERE id=:id AND version=:previous', args)
                if count != 1:
                    raise ApiError(409, 'قطعه هم‌زمان تغییر کرده است؛ دوباره پیش‌نمایش بگیرید.')
                audit(c, user, 'import-create' if old is None else 'import-update', 'part', identifier,
                      {'currency': 'IRR', 'fileSha256': digest, 'sheet': result['sheet'], 'row': item['row'],
                       'changes': item['changes'], 'version': previous + 1})
            summary = result['summary']
            audit(c, user, 'import-xlsx', 'parts', digest, {'currency': 'IRR', 'sheet': result['sheet'], 'summary': summary})
            return {'inserted': summary['new'], 'updated': summary['updated'], 'unchanged': summary['unchanged']}

        return MasterDataService(self.db)._run(operation)

    @staticmethod
    def _snapshot(c, codes, lock):
        result = {}
        ordered = sorted(codes)
        for start in range(0, len(ordered), 200):
            args = {f'code_{i}': code for i, code in enumerate(ordered[start:start + 200])}
            if not args:
                continue
            placeholders = ','.join(':' + key for key in args)
            suffix = ' ORDER BY code,id' + (' FOR UPDATE' if lock and not c.is_sqlite else '')
            for row in c.query('SELECT ' + _DB_FIELDS + ' FROM parts WHERE code IN (' + placeholders + ')' + suffix, args):
                result[row['code']] = dict(row)
        return result

    @staticmethod
    def _numeric_code_conflicts(c, codes):
        """Flag lost zero padding without treating a similar code as a match."""
        if not codes:
            return set()
        conflicts = set()
        for row in c.query("SELECT code FROM parts WHERE code LIKE '0%' ORDER BY code"):
            code = row['code']
            if re.fullmatch(r'[0-9]+', code):
                numeric = code.lstrip('0') or '0'
                if numeric in codes and code != numeric:
                    conflicts.add(numeric)
        return conflicts

    def _plan(self, c, workbook, content, selected, lock=False):
        if selected is not None:
            selected = text(selected, maximum=200, field='نام برگه')
        sheets = [{'name': item['name'], 'eligible': bool(item['header'] and item['visible'])} for item in workbook]
        candidates = [item for item in workbook if item['header'] and item['visible']]
        chosen = next((item for item in candidates if item['name'] == selected), None) if selected else candidates[0] if len(candidates) == 1 else None
        result = {'currency': 'IRR', 'sheets': sheets, 'sheet': None, 'headerRow': None, 'columns': {},
                  'summary': {'new': 0, 'updated': 0, 'unchanged': 0, 'invalid': 0, 'total': 0},
                  'rows': [], 'fingerprint': None, 'canApply': False, 'warnings': []}
        if chosen is None:
            if selected:
                raise ApiError(400, 'برگهٔ انتخاب‌شده وجود ندارد یا ستون کد و مشخصات قابل تشخیص ندارد.')
            result['warnings'].append('چند برگهٔ قابل ورود پیدا شد؛ برگهٔ مورد نظر را انتخاب کنید.' if candidates else 'ستون کد و نام یا قیمت پیدا نشد؛ عنوان ستون‌ها را روشن و در ۳۰ ردیف اول فایل قرار دهید.')
            return result, []
        header = chosen['header']
        result.update(sheet=chosen['name'], headerRow=header['row'])
        if header['ambiguousRows']:
            result['warnings'].append('بیش از یک ردیف عنوان مشابه تشخیص داده شد؛ عنوان‌های تکراری را از ابتدای فایل حذف کنید.')
            return result, []
        if any(len(columns) != 1 for columns in header['fields'].values()):
            result['warnings'].append('چند ستون برای یک مشخصه تشخیص داده شد؛ عنوان ستون‌های کد، نام و قیمت باید یکتا باشد.')
            return result, []
        fields = {field: values[0] for field, values in header['fields'].items()}
        result['columns'] = {field: {'column': column_name(col), 'label': label} for field, (col, label) in fields.items()}
        preceding = [cell.value for number, cells in chosen['rows'] if number <= header['row'] for cell in cells.values()]
        if 'unitCost' in fields and any(unsupported_currency(value) for value in preceding):
            result['warnings'].append('واحد قیمت غیرریالی در عنوان فایل دیده شد؛ این ورودی فقط قیمت ریالی می‌پذیرد. واحد قیمت را در فایل اصلاح کنید.')
            return result, []
        if 'unitCost' not in fields:
            result['warnings'].append('ستون قیمت در این فایل نیست؛ قیمت‌های قبلی حفظ می‌شوند و قیمت قطعات جدید نامشخص می‌ماند.')
        rows = []
        for number, cells in chosen['rows']:
            if number <= header['row']:
                continue
            selected_cells = {field: cells.get(col, Cell()) for field, (col, _) in fields.items()}
            if not any(cell.value.strip() or cell.formula for cell in selected_cells.values()):
                continue
            values, errors = {}, []
            for field, cell in selected_cells.items():
                try:
                    value = price(cell) if field == 'unitCost' else text(cell_text(cell, field), maximum=_LIMITS[field], field=fields[field][1])
                    if value is not None and value != '':
                        values[field] = value
                except ApiError as error:
                    errors.append(fields[field][1] + ': ' + error.message)
            if not values.get('code'):
                errors.append('کد قطعه خالی یا نامعتبر است.')
            code_cell = selected_cells['code']
            # General/numeric cells do not retain a text identifier's leading
            # zeroes. A fixed-width zero format remains an explicit identifier.
            numeric_code = code_cell.kind == 'n' and code_cell.number_format in ('', 'General', '@', '0')
            rows.append({'row': number, 'values': values, 'errors': errors, 'numericCode': numeric_code})
        if len(rows) > MAX_ROWS:
            raise ApiError(400, 'حداکثر ۲۰ هزار ردیف داده قابل ورود است.')
        counts = {}
        for item in rows:
            code = item['values'].get('code')
            counts[code] = counts.get(code, 0) + 1
        existing = self._snapshot(c, {item['values']['code'] for item in rows if item['values'].get('code')}, lock)
        numeric_conflicts = self._numeric_code_conflicts(c, {item['values']['code'] for item in rows if item['numericCode'] and item['values'].get('code')})
        plan, signature = [], []
        for item in rows:
            source, errors = item['values'], list(item['errors'])
            code = source.get('code', '')
            old = existing.get(code)
            before = {'code': old['code'], 'name': old['name'], 'specification': old['specification'], 'unit': old['unit'],
                      'unitCost': decimal_value(old['unit_cost']) if old['unit_cost'] is not None else None} if old else None
            values = dict(before or {'code': code, 'name': '', 'specification': '', 'unit': '', 'unitCost': None}, **source)
            if code and counts[code] > 1:
                errors.append('کد قطعه در همین برگه تکرار شده است؛ همهٔ ردیف‌های این کد را بررسی کنید.')
            if item['numericCode'] and code in numeric_conflicts:
                errors.append('کد عددی ممکن است صفرهای اول را از دست داده باشد؛ کد دقیق قطعه را به‌صورت Text در فایل وارد کنید.')
            if not old and not values['name']:
                errors.append('برای قطعهٔ جدید نام لازم است.')
            if old and old['version'] >= _MAX_VERSION:
                errors.append('ظرفیت نسخهٔ قطعه تکمیل شده است؛ مدیر سامانه بررسی کند.')
            if old and before['unit'] != values['unit']:
                used = c.scalar('SELECT EXISTS(SELECT 1 FROM order_parts WHERE part_id=:id) OR EXISTS(SELECT 1 FROM part_consumptions WHERE part_id=:id) OR EXISTS(SELECT 1 FROM stock_movements WHERE part_id=:id)', {'id': old['id']})
                if used:
                    errors.append('واحد قطعهٔ دارای سابقه ثابت است؛ واحد فایل را با واحد ثبت‌شده یکسان کنید.')
            changes = [{'field': field, 'from': before[field] if before else None, 'to': value} for field, value in values.items() if before is None or before[field] != value]
            status = 'invalid' if errors else 'new' if old is None else 'updated' if changes else 'unchanged'
            public = {'row': item['row'], 'code': code, 'name': values['name'], 'status': status, 'changes': changes, 'errors': errors}
            result['rows'].append(public)
            result['summary'][status] += 1
            result['summary']['total'] += 1
            plan.append(dict(public, values=values, old=old))
            signature.append({'row': item['row'], 'values': values, 'errors': errors, 'old': {'id': old['id'], 'version': old['version']} if old else None})
        result['fingerprint'] = hashlib.sha256(dumps({'file': hashlib.sha256(content).hexdigest(), 'sheet': result['sheet'], 'rows': signature}).encode('utf-8')).hexdigest()
        result['canApply'] = bool(rows and not result['summary']['invalid'] and (result['summary']['new'] or result['summary']['updated']))
        if not rows:
            result['warnings'].append('ردیف داده‌ای پس از عنوان‌ها پیدا نشد.')
        return result, plan
