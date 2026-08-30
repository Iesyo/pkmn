CREATE TABLE `team_folders` (
	`id` text PRIMARY KEY NOT NULL,
	`name` text NOT NULL,
	`sort_order` integer DEFAULT 0 NOT NULL,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `team_folders_name_idx` ON `team_folders` (`name`);
--> statement-breakpoint
ALTER TABLE `teams` ADD `folder_id` text REFERENCES team_folders(id) ON DELETE SET NULL;
