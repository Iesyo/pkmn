ALTER TABLE `teams` ADD `scope` text DEFAULT 'owned' NOT NULL;
ALTER TABLE `teams` ADD `creator` text DEFAULT '' NOT NULL;
ALTER TABLE `teams` ADD `source_url` text DEFAULT '' NOT NULL;
ALTER TABLE `teams` ADD `source_label` text DEFAULT '' NOT NULL;
ALTER TABLE `teams` ADD `scouting_format` text DEFAULT '' NOT NULL;
ALTER TABLE `teams` ADD `scouting_notes` text DEFAULT '' NOT NULL;

CREATE INDEX `teams_scope_updated_idx` ON `teams` (`scope`,`updated_at`);

-- These two folders are known creator/reference collections and are not owned teams.
-- Reclassify them in place so IDs, versions and parsed sets remain intact.
UPDATE `teams`
SET
  `scope` = 'scouting',
  `creator` = COALESCE(
    NULLIF(
      CASE
        WHEN TRIM(`teams`.`name`) LIKE '@% %'
          THEN SUBSTR(
            TRIM(`teams`.`name`),
            2,
            INSTR(SUBSTR(TRIM(`teams`.`name`), 2), ' ') - 1
          )
        ELSE ''
      END,
      ''
    ),
    NULLIF(TRIM((SELECT `name` FROM `team_folders` WHERE `id` = `teams`.`folder_id`)), ''),
    `creator`
  ),
  `source_label` = 'Migrado desde Teams',
  `scouting_format` = COALESCE(
    (SELECT `format`
     FROM `team_versions`
     WHERE `team_id` = `teams`.`id`
     ORDER BY `version_number` DESC, `minor_version` DESC, `created_at` DESC
     LIMIT 1),
    ''
  )
WHERE `folder_id` IN (
  SELECT `id`
  FROM `team_folders`
  WHERE UPPER(TRIM(`name`)) IN ('RIOPASER', 'LENVGC')
);

UPDATE `teams`
SET `folder_id` = NULL, `sort_order` = 0
WHERE `scope` = 'scouting';

DELETE FROM `team_folders`
WHERE UPPER(TRIM(`name`)) IN ('RIOPASER', 'LENVGC')
  AND NOT EXISTS (
    SELECT 1 FROM `teams` WHERE `teams`.`folder_id` = `team_folders`.`id`
  );
