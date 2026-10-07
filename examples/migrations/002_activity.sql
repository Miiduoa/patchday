CREATE TABLE activity (
  id INTEGER PRIMARY KEY,
  task_id INTEGER NOT NULL REFERENCES tasks(id),
  previous_state TEXT NOT NULL,
  next_state TEXT NOT NULL
);
CREATE TRIGGER record_task_state AFTER UPDATE OF state ON tasks
WHEN OLD.state <> NEW.state
BEGIN
  INSERT INTO activity(task_id, previous_state, next_state)
  VALUES (NEW.id, OLD.state, NEW.state);
END;
UPDATE tasks SET state = 'done' WHERE id = 1;
