ALTER TABLE `matches` ADD `origin` text DEFAULT 'champions' NOT NULL;
--> statement-breakpoint
ALTER TABLE `matches` ADD `replay_artifact_json` text;
--> statement-breakpoint
UPDATE `matches`
SET `origin` = CASE
  WHEN `replay_url` <> '' THEN 'showdown'
  ELSE 'champions'
END;
