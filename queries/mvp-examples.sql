-- Counts per captured version.
SELECT
  version,
  JSON_EXTRACT(normalized_counts, '$.system_variables') AS system_variables,
  JSON_EXTRACT(normalized_counts, '$.show_config') AS show_config,
  JSON_EXTRACT(normalized_counts, '$.show_config_tidb') AS tidb_config,
  JSON_EXTRACT(normalized_counts, '$.show_config_tikv') AS tikv_config,
  JSON_EXTRACT(normalized_counts, '$.show_config_pd') AS pd_config,
  JSON_EXTRACT(normalized_counts, '$.show_config_tiflash') AS tiflash_config
FROM capture_versions
ORDER BY major, minor, patch;

-- History of one system variable.
SELECT
  sv.version,
  variable_scope,
  default_value,
  current_value,
  is_noop
FROM system_variables AS sv
JOIN capture_versions AS cv ON cv.version = sv.version
WHERE variable_name = 'tidb_enable_async_commit'
ORDER BY cv.major, cv.minor, cv.patch;

-- System variables whose default value changes across captured versions.
SELECT
  variable_name,
  COUNT(*) AS versions_seen,
  COUNT(DISTINCT COALESCE(default_value, '<NULL>')) AS distinct_defaults,
  GROUP_CONCAT(DISTINCT CONCAT(version, '=', COALESCE(default_value, '<NULL>')) ORDER BY version SEPARATOR '; ') AS default_history
FROM system_variables
GROUP BY variable_name
HAVING distinct_defaults > 1
ORDER BY variable_name;

-- History of one component config.
SELECT
  cc.version,
  cc.component,
  cc.name,
  cc.value
FROM component_configs AS cc
JOIN capture_versions AS cv ON cv.version = cc.version
WHERE cc.component = 'tidb'
  AND cc.name = 'new_collations_enabled_on_first_bootstrap'
ORDER BY cv.major, cv.minor, cv.patch;

-- Component config values that vary across captured versions.
SELECT
  component,
  name,
  COUNT(*) AS rows_seen,
  COUNT(DISTINCT COALESCE(value, '<NULL>')) AS distinct_values,
  GROUP_CONCAT(DISTINCT CONCAT(version, '=', COALESCE(value, '<NULL>')) ORDER BY version SEPARATOR '; ') AS value_history
FROM component_configs
GROUP BY component, name
HAVING distinct_values > 1
ORDER BY component, name;

-- Config items present in one version but absent in another.
SELECT
  newer.component,
  newer.name
FROM component_configs AS newer
LEFT JOIN component_configs AS older
  ON older.component = newer.component
 AND older.name = newer.name
 AND older.version = 'v8.1.2'
WHERE newer.version = 'v8.5.6'
  AND older.version IS NULL
ORDER BY newer.component, newer.name;
