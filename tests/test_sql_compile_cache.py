"""Only immutable SQL structure may be cached; credentials and rows may not."""
import sqlite3
import unittest

from pak_maintenance.database import Connection, _compile_sql_cached, compile_sql


class SqlCompileCacheTests(unittest.TestCase):
    def setUp(self):
        _compile_sql_cached.cache_clear()

    def tearDown(self):
        _compile_sql_cached.cache_clear()

    def test_repeated_structure_is_compiled_once_per_provider(self):
        sql = "SELECT :person,:person,':literal',100%2 -- :comment\n"
        sqlite = compile_sql(sql, postgresql=False)
        self.assertEqual(sqlite, compile_sql(sql, postgresql=False))
        postgres = compile_sql(sql, postgresql=True)
        self.assertIn("%(person)s", postgres[0])
        self.assertIn("100%%2", postgres[0])
        self.assertEqual(postgres[1], ("person",))
        self.assertEqual(sqlite[1], ("person",))
        info = _compile_sql_cached.cache_info()
        self.assertEqual((info.misses, info.hits, info.currsize, info.maxsize), (2, 1, 2, 1024))

    def test_each_execution_uses_fresh_bindings_and_fresh_rows(self):
        raw = sqlite3.connect(":memory:")
        connection = Connection(raw, is_sqlite=True)
        try:
            statement = "SELECT :password AS result"
            first = connection.one(statement, {"password": "private-first"})
            second = connection.one(statement, {"password": "private-second"})
            self.assertEqual(first, {"result": "private-first"})
            self.assertEqual(second, {"result": "private-second"})
            first["result"] = "mutated returned row"
            self.assertEqual(connection.one(statement, {"password": "private-third"}), {"result": "private-third"})
            structure = compile_sql(statement, postgresql=False)
            self.assertNotIn("private-", str(structure))
            with self.assertRaisesRegex(ValueError, "Missing SQL parameter"):
                connection.one(statement, {})
            self.assertEqual(_compile_sql_cached.cache_info().currsize, 1)
        finally:
            raw.close()

    def test_cache_eviction_is_bounded_and_no_statement_result_is_reused(self):
        for number in range(1100):
            compile_sql("SELECT :bound AS value /* static variant " + str(number) + " */", postgresql=False)
        info = _compile_sql_cached.cache_info()
        self.assertEqual(info.currsize, 1024)
        self.assertEqual(info.misses, 1100)
        compile_sql("SELECT :bound AS value /* static variant 0 */", postgresql=False)
        self.assertEqual(_compile_sql_cached.cache_info().misses, 1101)

    def test_public_input_errors_are_validated_before_cache_lookup(self):
        for invalid in (None, [], {}, 1, True, "", " \r\n", "SELECT '\x00'"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                compile_sql(invalid, postgresql=False)
        self.assertEqual(_compile_sql_cached.cache_info().currsize, 0)
        # Preserve the existing boolean-like provider behavior without passing an
        # unhashable caller value into functools' cache key.
        self.assertEqual(compile_sql("SELECT :a", postgresql=[]), ("SELECT :a", ("a",)))
        for invalid in ("SELECT 'unterminated", "SELECT /* unterminated", "SELECT $tag$unterminated"):
            with self.subTest(sql=invalid), self.assertRaises(ValueError):
                compile_sql(invalid, postgresql=True)
        self.assertEqual(_compile_sql_cached.cache_info().currsize, 1)


if __name__ == "__main__":
    unittest.main()
