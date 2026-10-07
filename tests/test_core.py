import hashlib
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from patchday.core import InputError, rehearse, statements
from patchday.cli import main
from patchday.report import html_report

class RehearsalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / 'source.db'
        with closing(sqlite3.connect(self.db)) as db:
            db.executescript("CREATE TABLE items(id INTEGER PRIMARY KEY, name TEXT NOT NULL); INSERT INTO items VALUES (1,'one'), (2,'two');")
        self.before = self.digest()

    def tearDown(self):
        self.tmp.cleanup()

    def digest(self):
        return hashlib.sha256(self.db.read_bytes()).hexdigest()

    def run_sql(self, *scripts, **kwargs):
        paths = []
        for i, sql in enumerate(scripts):
            path = self.root / f'{i:03}.sql'
            path.write_text(sql, encoding='utf-8')
            paths.append(path)
        result = rehearse(self.db, paths, **kwargs)
        self.assertEqual(self.before, self.digest(), 'Source bytes changed')
        return result

    def test_add_column_and_index_report(self):
        r = self.run_sql('ALTER TABLE items ADD COLUMN label TEXT;', 'CREATE INDEX item_name ON items(name);')
        self.assertEqual(r['status'], 'passed')
        self.assertTrue(r['checks']['passed'])
        self.assertEqual({c['object'] for c in r['changes']}, {'table:items', 'index:item_name'})
        self.assertEqual(r['changes'][1]['after']['rows'], 2)

    def test_failure_rolls_back_previous_files(self):
        r = self.run_sql('CREATE TABLE added(id);', 'ALTER TABLE items ADD COLUMN required TEXT NOT NULL;')
        self.assertEqual(r['status'], 'failed')
        self.assertTrue(r['rolled_back'])
        self.assertEqual([s['status'] for s in r['steps']], ['rolled_back', 'failed'])
        self.assertEqual(r['changes'], [])

    def test_falling_count_requires_review(self):
        r = self.run_sql('DELETE FROM items WHERE id=1;')
        self.assertEqual(r['status'], 'review')
        self.assertIn('2 → 1', r['risks'][0])

    def test_dropped_table_requires_review(self):
        r = self.run_sql('DROP TABLE items;')
        self.assertEqual(r['status'], 'review')
        self.assertIn('removed', r['risks'][0])

    def test_dropped_populated_column_requires_review_without_row_loss(self):
        r = self.run_sql('ALTER TABLE items DROP COLUMN name;')
        self.assertEqual(r['status'], 'review')
        change = r['changes'][0]
        self.assertEqual(change['before']['rows'], change['after']['rows'])
        self.assertEqual(change['before']['columns'], ['id', 'name'])
        self.assertEqual(change['after']['columns'], ['id'])
        self.assertIn('"name"', r['risks'][0])
        self.assertIn('removed or renamed', r['risks'][0])
        self.assertIn('&quot;name&quot;', html_report(r))

    def test_rebuilt_table_with_same_rows_still_reports_missing_column(self):
        r = self.run_sql('''CREATE TABLE replacement(id INTEGER PRIMARY KEY);
INSERT INTO replacement SELECT id FROM items;
DROP TABLE items;
ALTER TABLE replacement RENAME TO items;''')
        self.assertEqual(r['status'], 'review')
        self.assertEqual(r['changes'][0]['after']['rows'], 2)
        self.assertIn('"name"', r['risks'][0])

    def test_column_rename_conservatively_requires_review(self):
        r = self.run_sql('ALTER TABLE items RENAME COLUMN name TO title;')
        self.assertEqual(r['status'], 'review')
        self.assertIn('removed or renamed', r['risks'][0])

    def test_case_only_column_rename_is_not_loss(self):
        r = self.run_sql('ALTER TABLE items RENAME COLUMN name TO NAME;')
        self.assertEqual(r['status'], 'passed')
        self.assertEqual(r['risks'], [])

    def test_generated_and_quoted_columns_are_inspected(self):
        with closing(sqlite3.connect(self.db)) as db:
            db.executescript('''CREATE TABLE "odd""table" (
                "source" TEXT, "derived""value" TEXT GENERATED ALWAYS AS (upper(source)) VIRTUAL);
                INSERT INTO "odd""table"(source) VALUES ('one');''')
        self.before = self.digest()
        r = self.run_sql('ALTER TABLE "odd""table" DROP COLUMN "derived""value";')
        self.assertEqual(r['status'], 'review')
        change = r['changes'][0]
        self.assertEqual(change['before']['columns'], ['source', 'derived"value'])
        self.assertEqual(change['after']['columns'], ['source'])
        self.assertIn('"derived""value"', r['risks'][0])

    def test_unicode_column_names_are_not_casefolded(self):
        with closing(sqlite3.connect(self.db)) as db:
            db.executescript('CREATE TABLE unicode_names("ß", "ss"); INSERT INTO unicode_names VALUES(1,2);')
        self.before = self.digest()
        r = self.run_sql('ALTER TABLE unicode_names DROP COLUMN "ß";')
        self.assertEqual(r['status'], 'review')
        self.assertIn('"ß"', r['risks'][0])

    def test_triggers_and_quoted_semicolons(self):
        r = self.run_sql("""CREATE TABLE audit(value TEXT);
CREATE TRIGGER record AFTER UPDATE ON items BEGIN
 INSERT INTO audit VALUES ('one;two');
 INSERT INTO audit VALUES (NEW.name);
END;
UPDATE items SET name='new' WHERE id=1;""")
        self.assertEqual(r['status'], 'passed')
        self.assertEqual(r['steps'][0]['statements'], 3)
        self.assertEqual(r['steps'][0]['changed_rows'], 3)
        self.assertEqual(next(c['after']['rows'] for c in r['changes'] if c['name']=='audit'), 2)

    def test_final_statement_without_semicolon_and_comments(self):
        r = self.run_sql("-- a comment\nUPDATE items SET name='new' WHERE id=1\n-- tail")
        self.assertEqual(r['status'], 'passed')

    def test_immediate_foreign_key_violation(self):
        r = self.run_sql('CREATE TABLE child(parent REFERENCES items(id)); INSERT INTO child VALUES(99);')
        self.assertEqual(r['status'], 'failed')

    def test_deferred_foreign_key_violation(self):
        r = self.run_sql('CREATE TABLE child(parent REFERENCES items(id) DEFERRABLE INITIALLY DEFERRED); INSERT INTO child VALUES(99);')
        self.assertEqual(r['status'], 'failed')
        self.assertFalse(r['checks']['passed'])

    def test_deferred_fk_resolved_by_later_migration(self):
        r = self.run_sql('CREATE TABLE child(parent REFERENCES items(id) DEFERRABLE INITIALLY DEFERRED); INSERT INTO child VALUES(99);', "INSERT INTO items VALUES(99,'later');")
        self.assertEqual(r['status'], 'passed')

    def test_baseline_fk_corruption_rejected(self):
        with closing(sqlite3.connect(self.db)) as db:
            db.executescript('CREATE TABLE child(parent REFERENCES items(id)); INSERT INTO child VALUES(99);')
        self.before = self.digest()
        r = self.run_sql('SELECT 1;')
        self.assertEqual(r['status'], 'failed')
        self.assertEqual(r['steps'], [])

    def test_attach_cannot_escape_snapshot(self):
        target = self.root / 'escaped.db'
        r = self.run_sql(f"ATTACH DATABASE '{target.as_posix()}' AS other;")
        self.assertEqual(r['status'], 'failed')
        self.assertFalse(target.exists())

    def test_vacuum_into_cannot_write_file(self):
        target = self.root / 'escaped.db'
        r = self.run_sql(f"VACUUM INTO '{target.as_posix()}';")
        self.assertEqual(r['status'], 'failed')
        self.assertFalse(target.exists())

    def test_transaction_savepoint_pragma_and_extension_denied(self):
        for sql in ['COMMIT;', 'BEGIN;', 'SAVEPOINT x;', 'PRAGMA foreign_keys=OFF;',
                    'PRAGMA ignore_check_constraints=ON;', 'PRAGMA writable_schema=ON;',
                    "SELECT load_extension('bad');"]:
            with self.subTest(sql=sql):
                self.assertEqual(self.run_sql(sql)['status'], 'failed')

    def test_add_checked_column_can_run_sqlites_internal_quick_check(self):
        r = self.run_sql("ALTER TABLE items ADD COLUMN state TEXT NOT NULL DEFAULT 'open' CHECK(state IN ('open','done'));")
        self.assertEqual(r['status'], 'passed')
        self.assertTrue(r['checks']['passed'])

    def test_add_checked_column_rejects_invalid_existing_rows(self):
        r = self.run_sql('CREATE TABLE earlier(id);',
                         "ALTER TABLE items ADD COLUMN state TEXT DEFAULT 'invalid' CHECK(state='open');")
        self.assertEqual(r['status'], 'failed')
        self.assertIn('CHECK constraint failed', r['error'])
        self.assertTrue(r['rolled_back'])
        self.assertEqual([s['status'] for s in r['steps']], ['rolled_back', 'failed'])

    def test_timeout_interrupts_recursive_query(self):
        r = self.run_sql('WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n) SELECT sum(x) FROM n;', timeout=.1)
        self.assertEqual(r['status'], 'failed')

    def test_quoted_identifiers(self):
        r = self.run_sql('CREATE TABLE "odd""name" (id); INSERT INTO "odd""name" VALUES(1);')
        self.assertEqual(r['status'], 'passed')
        self.assertEqual(r['changes'][0]['after']['rows'], 1)

    def test_html_escapes_names_and_sql(self):
        r = self.run_sql('CREATE TABLE "<script>alert(1)</script>" (id);')
        html = html_report(r)
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)

    def test_wal_snapshot_includes_committed_wal_rows(self):
        db = sqlite3.connect(self.db)
        try:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute("INSERT INTO items VALUES(3,'wal row')")
            db.commit()
            self.before = self.digest()
            r = self.run_sql('ALTER TABLE items ADD COLUMN x;')
            self.assertEqual(r['changes'][0]['before']['rows'], 3)
        finally:
            db.close()

    def test_missing_database_does_not_create_file(self):
        missing = self.root / 'missing.db'
        with self.assertRaises(InputError):
            rehearse(missing, ['ignored.sql'])
        self.assertFalse(missing.exists())

    def test_duplicate_files_and_invalid_timeouts(self):
        sql = self.root / 'single.sql'
        sql.write_text('SELECT 1;')
        with self.assertRaises(InputError):
            rehearse(self.db, [sql, sql])
        for timeout in [0, -1, float('inf'), float('nan')]:
            with self.assertRaises(InputError):
                rehearse(self.db, [sql], timeout=timeout)

    def test_reports_never_overwrite_source(self):
        sql = self.root / 'single.sql'
        sql.write_text('SELECT 1;')
        self.assertEqual(main([str(self.db), str(sql), '--json', str(self.db)]), 2)
        self.assertEqual(self.before, self.digest())

    def test_cli_exit_codes_and_report(self):
        sql = self.root / 'single.sql'
        for query, code in [('SELECT 1;',0), ('DROP TABLE items;',3), ('SELECT no_such_column;',1)]:
            with self.subTest(query=query):
                sql.write_text(query)
                self.assertEqual(main([str(self.db), str(sql)]), code)
        sql.write_text('SELECT 1;')
        output = self.root / 'result.json'
        self.assertEqual(main([str(self.db), str(sql), '--json', str(output)]), 0)
        self.assertTrue(output.exists())

    def test_splitter_preserves_comment_semicolons(self):
        self.assertEqual(len(list(statements("-- ;\nSELECT ';'; /* ; */ SELECT 2;"))), 2)

    def test_invalid_database_rejected(self):
        self.db.write_text('not sqlite')
        sql = self.root / 'single.sql'
        sql.write_text('SELECT 1;')
        with self.assertRaises(InputError):
            rehearse(self.db, [sql])

if __name__ == '__main__':
    unittest.main()
