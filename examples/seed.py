"""Create a small synthetic database. Refuses to replace an existing file."""
from pathlib import Path
from contextlib import closing
import sqlite3
import sys

path = Path(sys.argv[1] if len(sys.argv) > 1 else "example.db")
with path.open("xb"):
    pass
with closing(sqlite3.connect(path)) as db:
    db.executescript("""
CREATE TABLE projects (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  archived INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE tasks (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id),
  title TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'open'
);
INSERT INTO projects VALUES (1, 'Website refresh', 0), (2, 'Autumn release', 0), (3, 'Old experiment', 1);
INSERT INTO tasks VALUES (1, 1, 'Review navigation', 'open'), (2, 1, 'Test keyboard flow', 'done'), (3, 2, 'Confirm schema changes', 'open');
""")
print(f"Created {path} with synthetic project data.")
