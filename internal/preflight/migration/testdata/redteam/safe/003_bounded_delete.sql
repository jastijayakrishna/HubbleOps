DELETE FROM events WHERE created_at < now() - interval '90 days';
