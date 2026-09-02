ALTER TABLE `teams` ADD `sort_order` integer DEFAULT 0 NOT NULL;
--> statement-breakpoint
WITH ranked AS (
  SELECT
    id,
    ROW_NUMBER() OVER (
      PARTITION BY folder_id
      ORDER BY updated_at DESC, name COLLATE NOCASE ASC, id ASC
    ) - 1 AS next_sort_order
  FROM teams
)
UPDATE teams
SET sort_order = (
  SELECT ranked.next_sort_order
  FROM ranked
  WHERE ranked.id = teams.id
);
