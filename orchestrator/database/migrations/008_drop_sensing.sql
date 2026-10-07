-- 'Sensing and monitoring' was deployed briefly before the maintainer
-- chose 'Instrumentation and control' instead (2026-10-07). Remove it,
-- with any work queued for it, unless research on it has started.
DELETE FROM jobs j USING need_categories c
WHERE c.name = 'Sensing and monitoring'
  AND j.status = 'queued'
  AND j.input->>'category_id' = c.id::text;

DELETE FROM need_categories c
WHERE c.name = 'Sensing and monitoring'
  AND NOT EXISTS (SELECT 1 FROM need_briefs b WHERE b.category_id = c.id)
  AND NOT EXISTS (SELECT 1 FROM projects p WHERE p.category_id = c.id)
  AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.input->>'category_id' = c.id::text);
