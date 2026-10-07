-- Existing rows have no owner; the NOT NULL addition must fail.
ALTER TABLE tasks ADD COLUMN owner TEXT NOT NULL;
