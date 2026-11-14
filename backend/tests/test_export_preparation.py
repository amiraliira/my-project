"""Prepared CSV consistency, bounded resources, and errors before HTTP success."""
from pathlib import Path
from contextlib import contextmanager
import shutil
import unittest
from unittest.mock import patch
from uuid import uuid4

from pak_maintenance.api import safe_error
from pak_maintenance.common import ApiError
from pak_maintenance.exports import ExportStream


class PreparedExportTests(unittest.TestCase):
    @contextmanager
    def temporary_directory(self):
        root = Path(__file__).resolve().parent / '.export-preparation-fixtures'
        root.mkdir(exist_ok=True)
        directory = root / uuid4().hex
        directory.mkdir(mode=0o755)
        try:
            yield directory
        finally:
            if directory.resolve().parent != root.resolve() or directory.is_symlink():
                raise RuntimeError('Unexpected synthetic fixture cleanup path')
            shutil.rmtree(directory)
            if not any(root.iterdir()):
                root.rmdir()

    def test_database_generator_is_finished_before_slow_download(self):
        events = []
        def rows():
            try:
                events.append('snapshot opened')
                for value in range(1000):
                    yield (value, '=SUM(A1:A2)')
            finally:
                events.append('snapshot closed')
        with self.temporary_directory() as directory:
            stream = ExportStream(['row', 'text'], rows()).prepare(Path(directory) / 'exports')
            self.assertEqual(events, ['snapshot opened', 'snapshot closed'])
            try:
                data = b''.join(stream)
            finally:
                stream.close()
            self.assertEqual(len(data.splitlines()), 1001)
            self.assertIn(b"'=SUM(A1:A2)", data)
            self.assertEqual(list((Path(directory) / 'exports').iterdir()), [])

    def test_size_failure_closes_snapshot_and_returns_slot(self):
        closed = []
        def rows():
            try:
                yield ('x' * 100,)
            finally:
                closed.append(True)
        with self.temporary_directory() as directory:
            with self.assertRaises(ApiError) as error:
                ExportStream(['h'], rows()).prepare(directory, max_bytes=20)
            self.assertEqual(error.exception.status, 413)
            self.assertEqual(closed, [True])
            streams = [ExportStream(['h'], []) for _ in range(4)]
            for stream in streams:
                stream.close()
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_download_does_not_reopen_snapshot_and_disk_spill_is_deleted(self):
        with self.temporary_directory() as directory:
            with patch('pak_maintenance.exports.EXPORT_MEMORY_BYTES', 1):
                stream = ExportStream(['h'], [('one',), ('two',)]).prepare(directory)
            self.assertTrue(stream._prepared._rolled)
            self.assertIn(b'two', b''.join(stream))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_query_failure_is_not_a_successful_partial_csv(self):
        def rows():
            yield ('first',)
            raise RuntimeError('private query detail')
        with self.temporary_directory() as directory:
            with self.assertRaises(RuntimeError):
                ExportStream(['h'], rows()).prepare(directory)
            self.assertEqual(list(Path(directory).iterdir()), [])
            streams = [ExportStream(['h'], []) for _ in range(4)]
            for stream in streams:
                stream.close()

    def test_known_capacity_or_statement_limits_return_retryable_safe_response(self):
        for state in ('53300', '55P03', '57014'):
            with self.subTest(state=state):
                error = RuntimeError('private query and password')
                error.sqlstate = state
                response = safe_error(error)
                self.assertEqual(response.status, 503)
                self.assertEqual(dict(response.headers)['retry-after'], '2')
                self.assertNotIn('private', str(response.payload))


if __name__ == '__main__':
    unittest.main()
