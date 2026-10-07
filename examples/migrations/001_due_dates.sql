ALTER TABLE tasks ADD COLUMN due_date TEXT;
CREATE INDEX tasks_by_project ON tasks(project_id, state);
