-- The canonical Pokemon library is derived entirely from immutable Team Version snapshots.
-- Rebuild it once so hashes created before semantic normalization do not survive deployment.
DELETE FROM `pokemon_library_usages`;
--> statement-breakpoint
DELETE FROM `pokemon_library_versions`;
--> statement-breakpoint
DELETE FROM `pokemon_library_entries`;
