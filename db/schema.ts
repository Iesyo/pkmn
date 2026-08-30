import { sql } from "drizzle-orm";
import {
  index,
  integer,
  sqliteTable,
  text,
  uniqueIndex,
} from "drizzle-orm/sqlite-core";

export const teamFolders = sqliteTable(
  "team_folders",
  {
    id: text("id").primaryKey(),
    name: text("name").notNull(),
    sortOrder: integer("sort_order").notNull().default(0),
    createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  },
  (table) => [uniqueIndex("team_folders_name_idx").on(table.name)],
);

export const teams = sqliteTable("teams", {
  id: text("id").primaryKey(),
  name: text("name").notNull(),
  folderId: text("folder_id").references(() => teamFolders.id, { onDelete: "set null" }),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
});

export const appSettings = sqliteTable("app_settings", {
  key: text("key").primaryKey(),
  value: text("value").notNull(),
  updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
});

export const teamVersions = sqliteTable(
  "team_versions",
  {
    id: text("id").primaryKey(),
    teamId: text("team_id")
      .notNull()
      .references(() => teams.id, { onDelete: "cascade" }),
    versionNumber: integer("version_number").notNull(),
    minorVersion: integer("minor_version").notNull().default(0),
    format: text("format").notNull().default("gen9"),
    mechanicsJson: text("mechanics_json").notNull().default('["tera"]'),
    paste: text("paste").notNull(),
    pasteHash: text("paste_hash").notNull(),
    createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  },
  (table) => [
    uniqueIndex("team_versions_team_version_idx").on(
      table.teamId,
      table.versionNumber,
      table.minorVersion,
    ),
    uniqueIndex("team_versions_team_hash_idx").on(
      table.teamId,
      table.pasteHash,
    ),
  ],
);

export const pokemonSets = sqliteTable(
  "pokemon_sets",
  {
    id: text("id").primaryKey(),
    teamVersionId: text("team_version_id")
      .notNull()
      .references(() => teamVersions.id, { onDelete: "cascade" }),
    slot: integer("slot").notNull(),
    nickname: text("nickname").notNull(),
    species: text("species").notNull(),
    item: text("item").notNull().default(""),
    ability: text("ability").notNull().default(""),
    level: integer("level").notNull().default(50),
    teraType: text("tera_type"),
    mechanicsJson: text("mechanics_json").notNull().default("{}"),
    evs: text("evs").notNull().default(""),
    nature: text("nature").notNull().default(""),
    movesJson: text("moves_json").notNull(),
    typesJson: text("types_json").notNull(),
  },
  (table) => [
    uniqueIndex("pokemon_sets_version_slot_idx").on(
      table.teamVersionId,
      table.slot,
    ),
    index("pokemon_sets_version_idx").on(table.teamVersionId),
  ],
);

export const pokemonLibraryEntries = sqliteTable(
  "pokemon_library_entries",
  {
    id: text("id").primaryKey(),
    species: text("species").notNull(),
    speciesKey: text("species_key").notNull(),
    format: text("format").notNull(),
    createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  },
  (table) => [
    uniqueIndex("pokemon_library_entries_species_format_idx").on(
      table.speciesKey,
      table.format,
    ),
    index("pokemon_library_entries_species_idx").on(table.speciesKey),
  ],
);

export const pokemonLibraryVersions = sqliteTable(
  "pokemon_library_versions",
  {
    id: text("id").primaryKey(),
    entryId: text("entry_id")
      .notNull()
      .references(() => pokemonLibraryEntries.id, { onDelete: "cascade" }),
    versionNumber: integer("version_number").notNull(),
    setHash: text("set_hash").notNull(),
    paste: text("paste").notNull(),
    setJson: text("set_json").notNull(),
    sourceTeamVersionId: text("source_team_version_id").references(
      () => teamVersions.id,
      { onDelete: "set null" },
    ),
    createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  },
  (table) => [
    uniqueIndex("pokemon_library_versions_entry_version_idx").on(
      table.entryId,
      table.versionNumber,
    ),
    uniqueIndex("pokemon_library_versions_entry_hash_idx").on(
      table.entryId,
      table.setHash,
    ),
    index("pokemon_library_versions_entry_idx").on(table.entryId),
  ],
);

export const pokemonLibraryUsages = sqliteTable(
  "pokemon_library_usages",
  {
    id: text("id").primaryKey(),
    libraryVersionId: text("library_version_id")
      .notNull()
      .references(() => pokemonLibraryVersions.id, { onDelete: "cascade" }),
    teamVersionId: text("team_version_id")
      .notNull()
      .references(() => teamVersions.id, { onDelete: "cascade" }),
    slot: integer("slot").notNull(),
    createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  },
  (table) => [
    uniqueIndex("pokemon_library_usages_team_slot_idx").on(
      table.teamVersionId,
      table.slot,
    ),
    index("pokemon_library_usages_version_idx").on(table.libraryVersionId),
  ],
);

export const matches = sqliteTable(
  "matches",
  {
    id: text("id").primaryKey(),
    teamVersionId: text("team_version_id")
      .notNull()
      .references(() => teamVersions.id, { onDelete: "restrict" }),
    result: text("result", { enum: ["win", "loss"] }).notNull(),
    opponentName: text("opponent_name").notNull().default("Rival"),
    opponentPaste: text("opponent_paste").notNull().default(""),
    replayUrl: text("replay_url").notNull().default(""),
    selectedJson: text("selected_json").notNull().default("[]"),
    opponentSelectedJson: text("opponent_selected_json")
      .notNull()
      .default("[]"),
    opponentPicksJson: text("opponent_picks_json")
      .notNull()
      .default("[]"),
    leadJson: text("lead_json").notNull().default("[]"),
    movesUsedJson: text("moves_used_json"),
    rating: integer("rating"),
    notes: text("notes").notNull().default(""),
    playedAt: text("played_at").notNull(),
    createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  },
  (table) => [index("matches_version_played_idx").on(table.teamVersionId, table.playedAt)],
);

export const scoutingAnalyses = sqliteTable(
  "scouting_analyses",
  {
    id: text("id").primaryKey(),
    matchId: text("match_id")
      .notNull()
      .references(() => matches.id, { onDelete: "cascade" }),
    status: text("status", { enum: ["queued", "running", "complete", "error"] }).notNull().default("queued"),
    progress: integer("progress").notNull().default(0),
    stage: text("stage").notNull().default("En cola"),
    checkpointJson: text("checkpoint_json").notNull().default("{}"),
    resultJson: text("result_json"),
    calculatorRevision: text("calculator_revision").notNull().default("champions-v1"),
    error: text("error"),
    createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
    updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  },
  (table) => [
    uniqueIndex("scouting_analyses_match_idx").on(table.matchId),
    index("scouting_analyses_status_idx").on(table.status, table.updatedAt),
  ],
);