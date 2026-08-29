CREATE TABLE `scouting_analyses` (
	`id` text PRIMARY KEY NOT NULL,
	`match_id` text NOT NULL,
	`status` text DEFAULT 'queued' NOT NULL,
	`progress` integer DEFAULT 0 NOT NULL,
	`stage` text DEFAULT 'En cola' NOT NULL,
	`checkpoint_json` text DEFAULT '{}' NOT NULL,
	`result_json` text,
	`calculator_revision` text DEFAULT 'champions-v1' NOT NULL,
	`error` text,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	`updated_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	FOREIGN KEY (`match_id`) REFERENCES `matches`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE UNIQUE INDEX `scouting_analyses_match_idx` ON `scouting_analyses` (`match_id`);--> statement-breakpoint
CREATE INDEX `scouting_analyses_status_idx` ON `scouting_analyses` (`status`,`updated_at`);