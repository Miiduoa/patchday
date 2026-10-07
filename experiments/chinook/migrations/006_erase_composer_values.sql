-- Deliberate blind spot: names and row counts survive, values do not.
UPDATE Track SET Composer = NULL WHERE Composer IS NOT NULL;
