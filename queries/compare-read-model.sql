-- TiDB System Variables comparison read model.
-- Change these two values before running.
-- Branch-specific lifecycle fields such as metadata.deprecated_since_versions
-- should be evaluated by the API layer after this row model is returned.
SET @from_version = 'v8.1.2';
SET @to_version = 'v8.5.6';

WITH item_keys AS (
  SELECT variable_name AS item_key FROM system_variables WHERE version = @from_version
  UNION
  SELECT variable_name AS item_key FROM system_variables WHERE version = @to_version
),
comparison_rows AS (
  SELECT
    CASE
      WHEN old.variable_name IS NULL THEN 'new'
      WHEN new.variable_name IS NULL THEN 'removed'
      WHEN NOT (
        old.variable_scope <=> new.variable_scope
        AND old.default_value <=> new.default_value
        AND old.min_value <=> new.min_value
        AND old.max_value <=> new.max_value
        AND old.possible_values <=> new.possible_values
        AND old.is_noop <=> new.is_noop
      ) THEN 'modified'
      ELSE 'unchanged'
    END AS status,
    'system_variables' AS content_type,
    'tidb' AS component,
    keys.item_key,
    COALESCE(meta.display_name, keys.item_key) AS display_name,
    COALESCE(meta.variable_scope, new.variable_scope, old.variable_scope) AS scope,
    meta.value_type,
    old.default_value AS from_value,
    new.default_value AS to_value,
    old.current_value AS from_current_value,
    new.current_value AS to_current_value,
    meta.new_since,
    meta.deprecated_since IS NOT NULL AND cv_to.major * 1000000 + cv_to.minor * 1000 + cv_to.patch >= cv_dep.major * 1000000 + cv_dep.minor * 1000 + cv_dep.patch AS is_deprecated,
    meta.deprecated_since,
    meta.removed_since,
    meta.replacement,
    meta.persists_to_cluster,
    meta.applies_to_set_var,
    meta.description,
    meta.docs_url,
    COALESCE(meta.source, 'variables_info') AS source,
    meta.metadata
  FROM item_keys AS keys
  LEFT JOIN system_variables AS old
    ON old.version = @from_version AND old.variable_name = keys.item_key
  LEFT JOIN system_variables AS new
    ON new.version = @to_version AND new.variable_name = keys.item_key
  LEFT JOIN config_item_metadata AS meta
    ON meta.content_type = 'system_variables'
   AND meta.component = 'tidb'
   AND meta.item_key = keys.item_key
  LEFT JOIN capture_versions AS cv_to
    ON cv_to.version = @to_version
  LEFT JOIN capture_versions AS cv_dep
    ON cv_dep.version = meta.deprecated_since
)
SELECT *
FROM comparison_rows
ORDER BY FIELD(status, 'new', 'removed', 'modified', 'unchanged'), item_key;

-- Summary for the same system-variable comparison.
WITH item_keys AS (
  SELECT variable_name AS item_key FROM system_variables WHERE version = @from_version
  UNION
  SELECT variable_name AS item_key FROM system_variables WHERE version = @to_version
),
comparison_rows AS (
  SELECT
    CASE
      WHEN old.variable_name IS NULL THEN 'new'
      WHEN new.variable_name IS NULL THEN 'removed'
      WHEN NOT (
        old.variable_scope <=> new.variable_scope
        AND old.default_value <=> new.default_value
        AND old.min_value <=> new.min_value
        AND old.max_value <=> new.max_value
        AND old.possible_values <=> new.possible_values
        AND old.is_noop <=> new.is_noop
      ) THEN 'modified'
      ELSE 'unchanged'
    END AS status
  FROM item_keys AS keys
  LEFT JOIN system_variables AS old
    ON old.version = @from_version AND old.variable_name = keys.item_key
  LEFT JOIN system_variables AS new
    ON new.version = @to_version AND new.variable_name = keys.item_key
)
SELECT
  COUNT(*) AS total,
  SUM(status = 'new') AS new_count,
  SUM(status = 'removed') AS removed_count,
  SUM(status = 'modified') AS modified_count,
  SUM(status = 'unchanged') AS unchanged_count
FROM comparison_rows;

-- Component config comparison read model.
-- Set @component to one of: tidb, tikv, pd, tiflash.
SET @component = 'tidb';
SET @content_type = CONCAT(@component, '_config');

WITH old_items AS (
  SELECT
    name AS item_key,
    CASE
      WHEN COUNT(*) = 1 THEN MAX(value)
      ELSE GROUP_CONCAT(CONCAT(instance, '=', COALESCE(value, '<NULL>')) ORDER BY instance SEPARATOR '\n')
    END AS value
  FROM component_configs
  WHERE version = @from_version
    AND component = @component
  GROUP BY name
),
new_items AS (
  SELECT
    name AS item_key,
    CASE
      WHEN COUNT(*) = 1 THEN MAX(value)
      ELSE GROUP_CONCAT(CONCAT(instance, '=', COALESCE(value, '<NULL>')) ORDER BY instance SEPARATOR '\n')
    END AS value
  FROM component_configs
  WHERE version = @to_version
    AND component = @component
  GROUP BY name
),
item_keys AS (
  SELECT item_key FROM old_items
  UNION
  SELECT item_key FROM new_items
)
SELECT
  CASE
    WHEN old.item_key IS NULL THEN 'new'
    WHEN new.item_key IS NULL THEN 'removed'
    WHEN NOT (old.value <=> new.value) THEN 'modified'
    ELSE 'unchanged'
  END AS status,
  @content_type AS content_type,
  @component AS component,
  keys.item_key,
  COALESCE(meta.display_name, keys.item_key) AS display_name,
  meta.value_type,
  old.value AS from_value,
  new.value AS to_value,
  meta.new_since,
  meta.deprecated_since,
  meta.removed_since,
  meta.replacement,
  meta.description,
  meta.docs_url,
  COALESCE(meta.source, 'show_config') AS source,
  meta.metadata
FROM item_keys AS keys
LEFT JOIN old_items AS old
  ON old.item_key = keys.item_key
LEFT JOIN new_items AS new
  ON new.item_key = keys.item_key
LEFT JOIN config_item_metadata AS meta
  ON meta.content_type = @content_type
 AND meta.component = @component
 AND meta.item_key = keys.item_key
ORDER BY FIELD(status, 'new', 'removed', 'modified', 'unchanged'), item_key;
