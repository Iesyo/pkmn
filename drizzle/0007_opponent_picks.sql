ALTER TABLE matches ADD COLUMN opponent_picks_json TEXT NOT NULL DEFAULT '[]';

UPDATE matches
SET opponent_picks_json = opponent_selected_json
WHERE replay_url = ''
  AND opponent_selected_json <> '[]'
  AND team_version_id IN (
    SELECT id FROM team_versions WHERE format = 'champions'
  );
