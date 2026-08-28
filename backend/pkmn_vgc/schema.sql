PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS teams (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS team_versions (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL CHECK (version_number > 0),
    paste TEXT NOT NULL,
    paste_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (team_id, version_number),
    UNIQUE (team_id, paste_hash)
);

CREATE TABLE IF NOT EXISTS pokemon_sets (
    id TEXT PRIMARY KEY,
    team_version_id TEXT NOT NULL REFERENCES team_versions(id) ON DELETE CASCADE,
    slot INTEGER NOT NULL CHECK (slot BETWEEN 1 AND 6),
    nickname TEXT NOT NULL,
    species TEXT NOT NULL,
    item TEXT NOT NULL DEFAULT '',
    ability TEXT NOT NULL DEFAULT '',
    level INTEGER NOT NULL DEFAULT 50,
    tera_type TEXT,
    evs TEXT NOT NULL DEFAULT '',
    nature TEXT NOT NULL DEFAULT '',
    moves_json TEXT NOT NULL,
    types_json TEXT NOT NULL,
    UNIQUE (team_version_id, slot)
);

CREATE TABLE IF NOT EXISTS matches (
    id TEXT PRIMARY KEY,
    team_version_id TEXT NOT NULL REFERENCES team_versions(id) ON DELETE RESTRICT,
    result TEXT NOT NULL CHECK (result IN ('win', 'loss')),
    opponent_name TEXT NOT NULL DEFAULT 'Rival',
    opponent_paste TEXT NOT NULL DEFAULT '',
    replay_url TEXT NOT NULL DEFAULT '',
    selected_json TEXT NOT NULL DEFAULT '[]',
    opponent_selected_json TEXT NOT NULL DEFAULT '[]',
    lead_json TEXT NOT NULL DEFAULT '[]',
    rating INTEGER,
    notes TEXT NOT NULL DEFAULT '',
    played_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS matches_version_played_idx
ON matches(team_version_id, played_at DESC);

CREATE TRIGGER IF NOT EXISTS team_versions_are_immutable
BEFORE UPDATE ON team_versions
BEGIN
    SELECT RAISE(ABORT, 'team versions are immutable');
END;

CREATE TRIGGER IF NOT EXISTS pokemon_sets_are_immutable
BEFORE UPDATE ON pokemon_sets
BEGIN
    SELECT RAISE(ABORT, 'pokemon sets are immutable');
END;
