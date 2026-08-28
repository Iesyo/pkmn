DROP INDEX `team_versions_team_version_idx`;--> statement-breakpoint
ALTER TABLE `team_versions` ADD `minor_version` integer DEFAULT 0 NOT NULL;--> statement-breakpoint
ALTER TABLE `team_versions` ADD `format` text DEFAULT 'gen9' NOT NULL;--> statement-breakpoint
ALTER TABLE `team_versions` ADD `mechanics_json` text DEFAULT '["tera"]' NOT NULL;--> statement-breakpoint
CREATE UNIQUE INDEX `team_versions_team_version_idx` ON `team_versions` (`team_id`,`version_number`,`minor_version`);--> statement-breakpoint
ALTER TABLE `pokemon_sets` ADD `mechanics_json` text DEFAULT '{}' NOT NULL;