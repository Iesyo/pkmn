CREATE TABLE `matches` (
	`id` text PRIMARY KEY NOT NULL,
	`team_version_id` text NOT NULL,
	`result` text NOT NULL,
	`opponent_name` text DEFAULT 'Rival' NOT NULL,
	`opponent_paste` text DEFAULT '' NOT NULL,
	`replay_url` text DEFAULT '' NOT NULL,
	`selected_json` text DEFAULT '[]' NOT NULL,
	`opponent_selected_json` text DEFAULT '[]' NOT NULL,
	`lead_json` text DEFAULT '[]' NOT NULL,
	`rating` integer,
	`notes` text DEFAULT '' NOT NULL,
	`played_at` text NOT NULL,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	FOREIGN KEY (`team_version_id`) REFERENCES `team_versions`(`id`) ON UPDATE no action ON DELETE restrict
);
--> statement-breakpoint
CREATE INDEX `matches_version_played_idx` ON `matches` (`team_version_id`,`played_at`);--> statement-breakpoint
CREATE TABLE `pokemon_sets` (
	`id` text PRIMARY KEY NOT NULL,
	`team_version_id` text NOT NULL,
	`slot` integer NOT NULL,
	`nickname` text NOT NULL,
	`species` text NOT NULL,
	`item` text DEFAULT '' NOT NULL,
	`ability` text DEFAULT '' NOT NULL,
	`level` integer DEFAULT 50 NOT NULL,
	`tera_type` text,
	`evs` text DEFAULT '' NOT NULL,
	`nature` text DEFAULT '' NOT NULL,
	`moves_json` text NOT NULL,
	`types_json` text NOT NULL,
	FOREIGN KEY (`team_version_id`) REFERENCES `team_versions`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE UNIQUE INDEX `pokemon_sets_version_slot_idx` ON `pokemon_sets` (`team_version_id`,`slot`);--> statement-breakpoint
CREATE INDEX `pokemon_sets_version_idx` ON `pokemon_sets` (`team_version_id`);--> statement-breakpoint
CREATE TABLE `team_versions` (
	`id` text PRIMARY KEY NOT NULL,
	`team_id` text NOT NULL,
	`version_number` integer NOT NULL,
	`paste` text NOT NULL,
	`paste_hash` text NOT NULL,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	FOREIGN KEY (`team_id`) REFERENCES `teams`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE UNIQUE INDEX `team_versions_team_version_idx` ON `team_versions` (`team_id`,`version_number`);--> statement-breakpoint
CREATE UNIQUE INDEX `team_versions_team_hash_idx` ON `team_versions` (`team_id`,`paste_hash`);--> statement-breakpoint
CREATE TABLE `teams` (
	`id` text PRIMARY KEY NOT NULL,
	`name` text NOT NULL,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	`updated_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL
);
--> statement-breakpoint
CREATE TRIGGER `team_versions_are_immutable`
BEFORE UPDATE ON `team_versions`
BEGIN
	SELECT RAISE(ABORT, 'team versions are immutable');
END;
--> statement-breakpoint
CREATE TRIGGER `pokemon_sets_are_immutable`
BEFORE UPDATE ON `pokemon_sets`
BEGIN
	SELECT RAISE(ABORT, 'pokemon sets are immutable');
END;
