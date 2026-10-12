"""Synthetic SQLite compatibility checks; never opens a production workspace."""
import ast
from contextlib import closing
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest


def load_check():
    source = Path(__file__).with_name('verify-data.py')
    tree = ast.parse(source.read_text())
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in ('has_siyuan_fts5_schema', 'sqlite_quick_check')]
    namespace = {'sqlite3': sqlite3, 're': re}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), 'exec'), namespace)
    return namespace['sqlite_quick_check']


class SQLiteCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.check = load_check()

    def test_normal_database_passes(self):
        with closing(sqlite3.connect(':memory:')) as conn:
            conn.execute('CREATE TABLE fixture (value TEXT)')
            conn.execute("INSERT INTO fixture VALUES ('synthetic')")
            self.assertEqual(self.check(conn), 'checked')

    def custom_database(self, path, tokenizer):
        # Install a valid FTS5 schema first, then emulate an application-created
        # custom-tokenizer schema which the host SQLite cannot instantiate.
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("CREATE VIRTUAL TABLE fixture USING fts5(value, tokenize='unicode61')")
            conn.execute('PRAGMA writable_schema=ON')
            conn.execute("UPDATE sqlite_master SET sql=replace(sql, 'unicode61', ?) WHERE name='fixture'",
                         (tokenizer,))
            conn.execute('PRAGMA schema_version=42')
            conn.commit()

    def emulate_host_quick_check(self, conn):
        class Connection:
            def execute(self, sql):
                # Newer local SQLite quick_check skips virtual tables. Force the
                # same real engine error observed in the VM's older quick_check.
                return conn.execute("SELECT * FROM fixture WHERE fixture MATCH 'synthetic'" if sql == 'PRAGMA quick_check' else sql)
        return Connection()

    def test_real_missing_siyuan_tokenizer_is_reported_not_checked(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'synthetic.db'
            self.custom_database(path, 'siyuan case_insensitive han_insensitive')
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as conn:
                self.assertEqual(self.check(self.emulate_host_quick_check(conn)), 'unsupported_custom_tokenizer')

    def test_other_missing_tokenizer_still_fails(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'synthetic.db'
            self.custom_database(path, 'different_custom_tokenizer')
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as conn:
                with self.assertRaises(sqlite3.OperationalError):
                    self.check(self.emulate_host_quick_check(conn))

    def test_no_schema_confirmation_still_fails(self):
        class Connection:
            def execute(self, sql):
                if sql == 'PRAGMA quick_check':
                    raise sqlite3.OperationalError('no such tokenizer: siyuan')
                return self
            def fetchall(self):
                return [('CREATE TABLE fixture (value TEXT)',)]
        with self.assertRaises(sqlite3.OperationalError):
            self.check(Connection())

    def test_other_operational_error_still_fails(self):
        class Connection:
            def execute(self, sql):
                raise sqlite3.OperationalError('database is locked')
        with self.assertRaises(sqlite3.OperationalError):
            self.check(Connection())

    def test_integrity_failure_still_fails(self):
        class Connection:
            def execute(self, sql):
                return self
            def fetchall(self):
                return [('synthetic integrity failure',)]
        with self.assertRaises(AssertionError):
            self.check(Connection())

    def test_other_database_error_still_fails(self):
        class Connection:
            def execute(self, sql):
                raise sqlite3.DatabaseError('database disk image is malformed')
        with self.assertRaises(sqlite3.DatabaseError):
            self.check(Connection())


if __name__ == '__main__':
    unittest.main()
