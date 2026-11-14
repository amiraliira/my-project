"""Inventory import on synthetic local data; never connects to a company DB."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from io import BytesIO
from pathlib import Path
import shutil
import sqlite3
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from pak_maintenance.common import ApiError, User, now
from pak_maintenance.database import Database, DatabaseConfig
from pak_maintenance.parts_import import PartsImportService
from pak_maintenance.parts_xlsx import Cell, cell_text, price, read_workbook

NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


def workbook(rows, *, sheets=None, extra=None, styles=None):
    sheets = sheets or [('انبار', rows)]
    output = BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('xl/workbook.xml', f'<workbook xmlns="{NS}" xmlns:r="{REL}"><sheets>' + ''.join(f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>' for i, (name, _) in enumerate(sheets, 1)) + '</sheets></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + ''.join(f'<Relationship Id="rId{i}" Type="{REL}/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(sheets) + 1)) + '</Relationships>')
        for index, (_, data) in enumerate(sheets, 1):
            body = []
            for row_index, values in enumerate(data, 1):
                cells = []
                for col, value in enumerate(values):
                    if value is None:
                        continue
                    meta = value if isinstance(value, dict) else {'value': value}
                    raw = str(meta.get('value', ''))
                    kind = meta.get('kind', 'n' if isinstance(meta.get('value'), (int, float)) else 'inlineStr')
                    formula = '<f>1+1</f>' if meta.get('formula') else ''
                    encoded = f'<is><t>{escape(raw)}</t></is>' if kind == 'inlineStr' else f'<v>{escape(raw)}</v>'
                    cells.append(f'<c r="{chr(65 + col)}{row_index}" t="{kind}" s="{meta.get("style", 0)}">{formula}{encoded}</c>')
                body.append(f'<row r="{row_index}">' + ''.join(cells) + '</row>')
            archive.writestr(f'xl/worksheets/sheet{index}.xml', f'<worksheet xmlns="{NS}"><sheetData>' + ''.join(body) + '</sheetData></worksheet>')
        if styles:
            archive.writestr('xl/styles.xml', styles)
        for name, value in (extra or {}).items():
            archive.writestr(name, value)
    return output.getvalue()


class PartsImportTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parent / '.parts-import-tests'
        self.directory = self.root / uuid4().hex
        self.directory.mkdir(parents=True)
        path = self.directory / 'fixture.db'
        schema_root = Path(__file__).parents[1] / 'schema'
        schema = (schema_root / 'sqlite-v10.sql').read_text(encoding='utf-8')
        schema += '\n' + '\n'.join(item.read_text(encoding='utf-8') for item in sorted(schema_root.glob('sqlite-*-guards.sql')))
        raw = sqlite3.connect(path)
        try:
            raw.executescript(schema)
            raw.commit()
        finally:
            raw.close()
        self.db = Database(DatabaseConfig('sqlite', str(path)))
        self.seed_parts()

    def seed_parts(self):
        self.service = PartsImportService(self.db)
        self.users = {}
        with self.db.transaction() as c:
            for role in ('admin', 'head', 'manager', 'planner', 'supervisor', 'technician'):
                self.users[role] = User(role, role, role, role, 'mechanical' if role in ('technician', 'supervisor') else '')
                c.execute("INSERT INTO users(id,code,name,role,specialty,active,password_hash,session_epoch) VALUES(:id,:id,:id,:role,:specialty,1,'',0)", {'id': role, 'role': role, 'specialty': self.users[role].specialty})
            c.execute("INSERT INTO parts(id,code,name,specification,unit,unit_cost,version) VALUES('leading','001','قطعه اول','مشخصه قبلی','عدد',12500,1)")
            c.execute("INSERT INTO parts(id,code,name,specification,unit,unit_cost,version) VALUES('short','1','قطعه دوم','','عدد',8000,1)")
        self.admin = self.users['admin']

    def tearDown(self):
        if self.directory.resolve().parent != self.root.resolve() or self.directory.is_symlink():
            raise RuntimeError('Unexpected test directory')
        shutil.rmtree(self.directory)
        try:
            self.root.rmdir()
        except OSError:
            pass

    def failure(self, status, operation):
        with self.assertRaises(ApiError) as caught:
            operation()
        self.assertEqual(caught.exception.status, status)

    def apply(self, data, sheet=None):
        preview = self.service.preview(self.admin, data, sheet)
        self.assertTrue(preview['canApply'], preview)
        return self.service.apply(self.admin, data, preview['fingerprint'], sheet)

    def test_autodetect_headers_after_title_exact_codes_blank_price_zero_and_rial(self):
        data = workbook([['گزارش انبار'], ['عنوان', 'فی واحد (ریال)', 'کد کالا', 'مشخصات فنی'], ['نام جدید', '', '۰۰۱', ''], ['', '۰', '1', ''], ['قطعه تازه', '۱۲٬۵۰۰٫۵۰', '0002', 'نو']])
        preview = self.service.preview(self.admin, data)
        self.assertEqual(preview['headerRow'], 2)
        self.assertEqual(preview['columns']['code']['column'], 'C')
        self.assertEqual(preview['summary'], {'new': 1, 'updated': 2, 'unchanged': 0, 'invalid': 0, 'total': 3})
        self.assertEqual(self.service.apply(self.admin, data, preview['fingerprint']), {'inserted': 1, 'updated': 2, 'unchanged': 0})
        with self.db.connect() as c:
            old = c.one("SELECT * FROM parts WHERE code='001'")
            self.assertEqual(old['unit_cost'], 12500)
            self.assertEqual(old['specification'], 'مشخصه قبلی')
            self.assertEqual(c.scalar("SELECT unit_cost FROM parts WHERE code='1'"), 0)
            self.assertEqual(c.scalar("SELECT unit_cost FROM parts WHERE code='0002'"), 12500.5)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM audit WHERE action='import-xlsx'"), 1)

    def test_no_price_column_preserves_price_and_missing_unit(self):
        data = workbook([['کد', 'عنوان', 'مشخصه فنی'], ['001', 'تغییر نام', ''], ['new-code', 'قطعه اول', '']])
        preview = self.service.preview(self.admin, data)
        self.assertTrue(preview['warnings'])
        self.apply(data)
        with self.db.connect() as c:
            row = c.one("SELECT * FROM parts WHERE code='001'")
            self.assertEqual((row['unit_cost'], row['unit']), (12500, 'عدد'))
            new = c.one("SELECT * FROM parts WHERE code='new-code'")
            self.assertIsNone(new['unit_cost'])
            self.assertEqual(new['name'], 'قطعه اول')

    def test_multiple_sheets_require_choice_and_column_order_is_free(self):
        data = workbook([], sheets=[('اول', [['کد', 'قیمت'], ['001', 10]]), ('دوم', [['قیمت', 'کد'], [20, '001']])])
        preview = self.service.preview(self.admin, data)
        self.assertIsNone(preview['sheet'])
        self.assertFalse(preview['canApply'])
        self.assertEqual(self.apply(data, 'دوم')['updated'], 1)
        self.failure(400, lambda: self.service.preview(self.admin, data, 'نام نادرست'))

    def test_duplicate_codes_duplicate_headers_and_ambiguous_header_rows_are_not_guessed(self):
        data = workbook([['کد', 'نام', 'قیمت'], ['001', 'الف', 10], ['001', 'ب', 20]])
        preview = self.service.preview(self.admin, data)
        self.assertEqual(preview['summary']['invalid'], 2)
        self.assertFalse(preview['canApply'])
        self.failure(400, lambda: self.service.apply(self.admin, data, preview['fingerprint']))
        for rows in ([['کد', 'کد کالا', 'قیمت'], ['001', '001', 20]], [['کد', 'قیمت'], ['کد', 'قیمت'], ['001', 20]]):
            preview = self.service.preview(self.admin, workbook(rows))
            self.assertFalse(preview['canApply'])
            self.assertTrue(preview['warnings'])

    def test_missing_new_name_and_bad_rows_block_entire_batch(self):
        data = workbook([['کد', 'نام', 'قیمت'], ['001', '', 10], ['new', '', 20]])
        preview = self.service.preview(self.admin, data)
        self.assertEqual(preview['summary']['invalid'], 1)
        self.failure(400, lambda: self.service.apply(self.admin, data, preview['fingerprint']))
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT unit_cost FROM parts WHERE code='001'"), 12500)

    def test_formulas_cached_values_errors_and_toman_are_rejected(self):
        for field in (0, 1):
            row = ['001', 100]
            row[field] = {'value': row[field], 'kind': 'n' if field else 'str', 'formula': True}
            preview = self.service.preview(self.admin, workbook([['کد', 'قیمت'], row]))
            self.assertEqual(preview['summary']['invalid'], 1)
        for title in ('قیمت تومان', 'قیمت (تومان)', 'قیمت تومن', 'unit price USD', 'قیمت دلار', 'price (€)'):
            self.assertFalse(self.service.preview(self.admin, workbook([['کد', title], ['001', 10]]))['canApply'])
        self.assertFalse(self.service.preview(self.admin, workbook([['Inventory prices in USD'], ['کد', 'قیمت'], ['001', 10]]))['canApply'])

    def test_stale_preview_modified_file_and_replay_do_not_write(self):
        data = workbook([['کد', 'قیمت'], ['001', 10]])
        preview = self.service.preview(self.admin, data)
        self.failure(409, lambda: self.service.apply(self.admin, workbook([['کد', 'قیمت'], ['001', 20]]), preview['fingerprint']))
        self.apply(data)
        self.failure(409, lambda: self.service.apply(self.admin, data, preview['fingerprint']))
        preview = self.service.preview(self.admin, workbook([['کد', 'قیمت'], ['001', 30]]))
        with self.db.transaction() as c:
            c.execute("UPDATE parts SET name='other',version=version+1 WHERE code='001'")
        self.failure(409, lambda: self.service.apply(self.admin, workbook([['کد', 'قیمت'], ['001', 30]]), preview['fingerprint']))

    def test_authority_rechecked_and_all_edit_roles_supported(self):
        data = workbook([['کد', 'قیمت'], ['001', 10]])
        for role in ('technician', 'supervisor'):
            self.failure(403, lambda role=role: self.service.preview(self.users[role], data))
        for role in ('head', 'manager', 'planner'):
            self.assertTrue(self.service.preview(self.users[role], data)['canApply'])
        preview = self.service.preview(self.admin, data)
        with self.db.transaction() as c:
            c.execute("UPDATE users SET session_epoch=1 WHERE id='admin'")
        self.failure(403, lambda: self.service.apply(self.admin, data, preview['fingerprint']))

    def test_used_unit_cannot_change_and_stock_is_not_imported(self):
        with self.db.transaction() as c:
            c.execute("INSERT INTO stock_movements(id,part_id,quantity,kind,reference,note,actor_id,created_at,quantity_units) VALUES('historic','leading',1,'historic','','','admin',:now,1000000)", {'now': now()})
        data = workbook([['کد', 'واحد', 'قیمت', 'موجودی'], ['001', 'کیلو', 100, 999]])
        preview = self.service.preview(self.admin, data)
        self.assertFalse(preview['canApply'])
        self.assertTrue(preview['rows'][0]['errors'])
        self.apply(workbook([['کد', 'قیمت', 'موجودی'], ['001', 100, 999]]))
        with self.db.connect() as c:
            self.assertEqual(c.scalar('SELECT COUNT(*) FROM stock_movements'), 1)

    def test_audit_failure_rolls_back_every_part(self):
        data = workbook([['کد', 'نام', 'قیمت'], ['001', '', 10], ['new', 'نو', 20]])
        preview = self.service.preview(self.admin, data)
        with self.db.transaction() as c:
            c.execute("CREATE TRIGGER fail_inventory_audit BEFORE INSERT ON audit WHEN NEW.action='import-xlsx' BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.apply(self.admin, data, preview['fingerprint'])
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT unit_cost FROM parts WHERE code='001'"), 12500)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM parts WHERE code='new'"), 0)
            self.assertEqual(c.scalar('SELECT COUNT(*) FROM audit'), 0)

    def test_concurrent_same_preview_commits_once(self):
        data = workbook([['کد', 'قیمت'], ['001', 10]])
        fingerprint = self.service.preview(self.admin, data)['fingerprint']
        barrier = threading.Barrier(2)
        def run():
            barrier.wait(timeout=5)
            try:
                self.service.apply(self.admin, data, fingerprint)
                return 200
            except ApiError as error:
                return error.status
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: run(), range(2)))
        self.assertEqual(sorted(results), [200, 409])

    def test_numeric_formats_precision_and_price_bounds(self):
        self.assertEqual(cell_text(Cell('12', 'n', False, '00000'), 'code'), '00012')
        for value in ('1e99', '1.2', '-1', '1234567890123456'):
            self.failure(400, lambda value=value: cell_text(Cell(value, 'n'), 'code'))
        for value in ('-1', '1000000000000.01', '0.001', 'NaN', '1,2', '۱۲ تومان'):
            self.failure(400, lambda value=value: price(Cell(value)))
        self.assertEqual(price(Cell('1e3', 'n')), 1000)
        self.assertEqual(price(Cell('12.3400', 'n')), Decimal('12.34'))
        self.assertEqual(price(Cell('0')), 0)
        self.assertIsNone(price(Cell('')))

    def test_numeric_code_cannot_guess_lost_leading_zeros_but_text_is_exact(self):
        with self.db.transaction() as c:
            c.execute("INSERT INTO parts(id,code,name,specification,unit,unit_cost,version) VALUES('padded','00123','قطعه صفر دار','','عدد',10,1)")
        numeric = workbook([['کد', 'نام', 'قیمت'], [123, 'قطعه جدید', 20]])
        preview = self.service.preview(self.admin, numeric)
        self.assertFalse(preview['canApply'])
        self.assertEqual(preview['rows'][0]['status'], 'invalid')
        self.failure(400, lambda: self.service.apply(self.admin, numeric, preview['fingerprint']))
        self.assertEqual(self.apply(workbook([['کد', 'نام', 'قیمت'], ['123', 'قطعه جدید', 20]]))['inserted'], 1)
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT unit_cost FROM parts WHERE code='00123'"), 10)
            self.assertEqual(c.scalar("SELECT unit_cost FROM parts WHERE code='123'"), 20)
        # Even an existing short code cannot resolve ambiguous numeric input.
        self.assertFalse(self.service.preview(self.admin, numeric)['canApply'])

    def test_explicit_zero_format_preserves_code_and_late_ambiguity_invalidates_preview(self):
        styles = f'<styleSheet xmlns="{NS}"><numFmts count="1"><numFmt numFmtId="164" formatCode="000"/></numFmts><cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="164"/></cellXfs></styleSheet>'
        data = workbook([['کد', 'قیمت'], [{'value': 1, 'style': 1}, 30]], styles=styles)
        self.assertEqual(self.apply(data)['updated'], 1)
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT unit_cost FROM parts WHERE code='001'"), 30)
            self.assertEqual(c.scalar("SELECT unit_cost FROM parts WHERE code='1'"), 8000)
        data = workbook([['کد', 'نام', 'قیمت'], [123, 'نو', 20]])
        preview = self.service.preview(self.admin, data)
        self.assertTrue(preview['canApply'])
        with self.db.transaction() as c:
            c.execute("INSERT INTO parts(id,code,name,specification,unit,unit_cost,version) VALUES('late','00123','سابق','','عدد',10,1)")
        self.failure(409, lambda: self.service.apply(self.admin, data, preview['fingerprint']))
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM parts WHERE code='123'"), 0)

    def test_negative_shared_string_and_style_indices_are_rejected(self):
        shared = f'<sst xmlns="{NS}"><si><t>001</t></si></sst>'
        for cell in ({'value': -1, 'kind': 's'}, {'value': '001', 'style': -1}, {'value': '001', 'style': 1}):
            self.failure(400, lambda cell=cell: read_workbook(workbook([['کد', 'نام'], [cell, 'نو']], extra={'xl/sharedStrings.xml': shared})))

    def test_zip_safety_and_external_references(self):
        self.failure(400, lambda: read_workbook(b'not an xlsx'))
        for extra in ({'../escape.xml': '<x/>'}, {'xl/vbaProject.bin': b'macro'}, {'xl/externalLinks/externalLink1.xml': '<x/>'}, {'xl/_rels/external.rels': '<Relationships><Relationship TargetMode="External" Target="http://example.invalid"/></Relationships>'}, {'xl/sharedStrings.xml': '<!DOCTYPE x [<!ENTITY y "x">]><x/>'}):
            self.failure(400, lambda extra=extra: read_workbook(workbook([['کد', 'نام']], extra=extra)))

    def test_known_original_inventory_is_read_only_and_has_no_price(self):
        path = Path(__file__).resolve().parents[3] / 'pak-transfer' / 'PAK-Complete-Transfer' / '02-Original-References' / 'inventory' / 'موجودی 106 1405.xlsx'
        if not path.exists():
            self.skipTest('Original company reference workbook is not a software dependency')
        data = path.read_bytes()
        parsed = read_workbook(data)
        self.assertEqual(len(parsed[0]['rows']), 8174)
        self.assertEqual(set(parsed[0]['header']['fields']), {'code', 'name', 'specification'})
        self.assertEqual(cell_text(parsed[0]['rows'][1][1][1], 'code'), '0050301157')


class PartsImportPostgresqlTests(unittest.TestCase):
    """Critical import mutations on an explicitly authorized loopback runtime."""
    seed_parts = PartsImportTests.seed_parts
    failure = PartsImportTests.failure
    apply = PartsImportTests.apply
    test_autodetect_headers_after_title_exact_codes_blank_price_zero_and_rial = PartsImportTests.test_autodetect_headers_after_title_exact_codes_blank_price_zero_and_rial
    test_numeric_code_cannot_guess_lost_leading_zeros_but_text_is_exact = PartsImportTests.test_numeric_code_cannot_guess_lost_leading_zeros_but_text_is_exact
    test_explicit_zero_format_preserves_code_and_late_ambiguity_invalidates_preview = PartsImportTests.test_explicit_zero_format_preserves_code_and_late_ambiguity_invalidates_preview
    test_stale_preview_modified_file_and_replay_do_not_write = PartsImportTests.test_stale_preview_modified_file_and_replay_do_not_write
    test_concurrent_same_preview_commits_once = PartsImportTests.test_concurrent_same_preview_commits_once
    test_used_unit_cannot_change_and_stock_is_not_imported = PartsImportTests.test_used_unit_cannot_change_and_stock_is_not_imported
    test_no_price_column_preserves_price_and_missing_unit = PartsImportTests.test_no_price_column_preserves_price_and_missing_unit

    def setUp(self):
        from pg_test_support import fresh_database
        config, self.owner_db = fresh_database(self, prefix='parts')
        self.db = Database(config)
        self.seed_parts()

    def test_summary_audit_failure_rolls_back_all_native_mutations(self):
        from pak_maintenance import parts_import
        data = workbook([['کد', 'نام', 'قیمت'], ['001', '', 10], ['new', 'نو', 20]])
        preview = self.service.preview(self.admin, data)
        original_audit = parts_import.audit
        def fail_summary(c, user, action, *args, **kwargs):
            if action == 'import-xlsx':
                raise RuntimeError('synthetic summary audit failure')
            return original_audit(c, user, action, *args, **kwargs)
        with patch('pak_maintenance.parts_import.audit', side_effect=fail_summary):
            with self.assertRaisesRegex(RuntimeError, 'synthetic summary audit failure'):
                self.service.apply(self.admin, data, preview['fingerprint'])
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT unit_cost FROM parts WHERE code='001'"), 12500)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM parts WHERE code='new'"), 0)
            self.assertEqual(c.scalar('SELECT COUNT(*) FROM audit'), 0)


if __name__ == '__main__':
    unittest.main()
