# A rehearsal is not a deployment

## Snapshot consistency

Copying only a `.db` file can omit committed data that remains in its write-ahead log. Patchday uses SQLite's backup API through a read-only source connection. A test holds a WAL connection open, commits a row, then confirms that the rehearsal sees that row without changing the main file's hash.

The snapshot represents a consistent database state. It does not predict later production writes. A deployment process still needs its own concurrency strategy and backups.

## One transaction owner

Each SQL file is split at complete SQLite statements, not at every semicolon. Quoted text and multi-statement trigger bodies survive. Statements are executed individually; `executescript()` is avoided because of its transaction behavior.

Patchday starts the transaction before installing the migration authorizer. The authorizer denies transaction and savepoint operations, attachment, PRAGMAs and virtual-table DDL. It is removed before tool-owned checks and commit/rollback. The full batch commits only inside the disposable connection. A failure rolls back all earlier files.

Foreign keys are enabled before the transaction. `foreign_key_check` runs against the starting and final snapshots; commit also exercises deferred constraints. Some real migrations require temporarily disabling foreign keys while rebuilding tables. Those workflows are intentionally unsupported in this version.

## What a diff contains

Schema objects are keyed by `(type, name)`. DDL text comes from `sqlite_schema`; table counts come from quoted identifiers. Changes include tables, indexes, views and triggers. A removed table or declining table count changes the outcome from `passed` to `review`, even when every SQL statement succeeds.

The comparison is structural plus row count, not a value-level diff. A table rename appears as removal plus addition. That deliberately requests review; the tool does not guess whether data was preserved elsewhere. Trigger writes contribute to per-step `total_changes`, so that metric is labeled row writes, not distinct affected rows.

## Resource and file boundaries

The progress handler interrupts long-running SQLite operations. Backup has its own deadline callback. This is a time budget, not a hard process/memory isolation boundary. Large database copies can consume substantial RAM before a timeout.

Reports use exclusive file creation. A preflight check improves the error message; `open(..., 'x')` is the final protection against a destination appearing between check and write. HTML uses escaping and no JavaScript. SQL schema text may still contain sensitive default literals and should be reviewed before publishing.

## Verification

The suite checks successful DDL, failed batch rollback, row-loss outcomes, immediate/deferred constraints, trigger semicolons, quoted identifiers, preserved source hashes, WAL contents, ATTACH/VACUUM escape attempts, blocked transaction controls, timeouts, report escaping and exit codes.

Missing evidence: production lock behavior, very large database performance, value-level equivalence, and SQLite extensions outside the standard runtime. Passing tests do not substitute for those measurements.
