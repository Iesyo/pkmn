import { sql } from "drizzle-orm";
import {
  index,
  integer,
  sqliteTable,
  text,
  uniqueIndex,
} from "drizzle-orm/sqlite-core";

export const teams = sqliteTable("teams", {
  id: text("id").primaryKey(),
  name: text("name").notNull(),
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
    leadJson: text("lead_json").notNull().default("[]"),
    rating: integer("rating"),
    notes: text("notes").notNull().default(""),
    playedAt: text("played_at").notNull(),
    createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  },
  (table) => [index("matches_version_played_idx").on(table.teamVersionId, table.playedAt)],
);
