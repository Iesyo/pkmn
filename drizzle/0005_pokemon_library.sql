CREATE TABLE `pokemon_library_entries` (
	`id` text PRIMARY KEY NOT NULL,
	`species` text NOT NULL,
	`species_key` text NOT NULL,
	`format` text NOT NULL,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `pokemon_library_entries_species_format_idx` ON `pokemon_library_entries` (`species_key`,`format`);
--> statement-breakpoint
CREATE INDEX `pokemon_library_entries_species_idx` ON `pokemon_library_entries` (`species_key`);
--> statement-breakpoint
CREATE TABLE `pokemon_library_versions` (
	`id` text PRIMARY KEY NOT NULL,
	`entry_id` text NOT NULL,
	`version_number` integer NOT NULL,
	`set_hash` text NOT NULL,
	`paste` text NOT NULL,
	`set_json` text NOT NULL,
	`source_team_version_id` text,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	FOREIGN KEY (`entry_id`) REFERENCES `pokemon_library_entries`(`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`source_team_version_id`) REFERENCES `team_versions`(`id`) ON UPDATE no action ON DELETE set null
);
--> statement-breakpoint
CREATE UNIQUE INDEX `pokemon_library_versions_entry_version_idx` ON `pokemon_library_versions` (`entry_id`,`version_number`);
--> statement-breakpoint
CREATE UNIQUE INDEX `pokemon_library_versions_entry_hash_idx` ON `pokemon_library_versions` (`entry_id`,`set_hash`);
--> statement-breakpoint
CREATE INDEX `pokemon_library_versions_entry_idx` ON `pokemon_library_versions` (`entry_id`);
--> statement-breakpoint
CREATE TABLE `pokemon_library_usages` (
	`id` text PRIMARY KEY NOT NULL,
	`library_version_id` text NOT NULL,
	`team_version_id` text NOT NULL,
	`slot` integer NOT NULL,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	FOREIGN KEY (`library_version_id`) REFERENCES `pokemon_library_versions`(`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`team_version_id`) REFERENCES `team_versions`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE UNIQUE INDEX `pokemon_library_usages_team_slot_idx` ON `pokemon_library_usages` (`team_version_id`,`slot`);
--> statement-breakpoint
CREATE INDEX `pokemon_library_usages_version_idx` ON `pokemon_library_usages` (`library_version_id`);
