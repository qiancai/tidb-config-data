CREATE TABLE IF NOT EXISTS capture_versions (
  version VARCHAR(32) NOT NULL,
  major INT,
  minor INT,
  patch INT,
  version_type VARCHAR(16),
  release_date DATE,
  release_note_url VARCHAR(512),
  selected_for_capture BOOLEAN NOT NULL DEFAULT TRUE,
  capture_scope VARCHAR(128),
  capture_reason TEXT,
  captured_at DATETIME(6),
  sanitized BOOLEAN NOT NULL,
  raw_dir VARCHAR(64),
  manifest_sha256 CHAR(64) NOT NULL,
  summary_sha256 CHAR(64),
  source_git_commit CHAR(40),
  normalized_counts JSON,
  manifest JSON,
  imported_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (version),
  KEY idx_capture_versions_release_date (release_date),
  KEY idx_capture_versions_semver (major, minor, patch)
);

CREATE TABLE IF NOT EXISTS capture_files (
  version VARCHAR(32) NOT NULL,
  path VARCHAR(512) NOT NULL,
  bytes BIGINT NOT NULL,
  sha256 CHAR(64) NOT NULL,
  file_role VARCHAR(64) NOT NULL,
  imported_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (version, path),
  KEY idx_capture_files_role (file_role),
  KEY idx_capture_files_sha256 (sha256)
);

CREATE TABLE IF NOT EXISTS cluster_instances (
  version VARCHAR(32) NOT NULL,
  component VARCHAR(32) NOT NULL,
  instance VARCHAR(191) NOT NULL,
  status_address VARCHAR(191),
  component_version VARCHAR(64),
  git_hash VARCHAR(64),
  start_time VARCHAR(64),
  uptime VARCHAR(64),
  server_id VARCHAR(64),
  raw_row JSON,
  imported_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (version, component, instance),
  KEY idx_cluster_instances_component (component)
);

CREATE TABLE IF NOT EXISTS system_variables (
  version VARCHAR(32) NOT NULL,
  variable_name VARCHAR(191) NOT NULL,
  variable_scope VARCHAR(191),
  default_value MEDIUMTEXT,
  current_value MEDIUMTEXT,
  min_value TEXT,
  max_value TEXT,
  possible_values MEDIUMTEXT,
  is_noop VARCHAR(16),
  raw_row JSON,
  imported_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (version, variable_name),
  KEY idx_system_variables_name (variable_name),
  KEY idx_system_variables_scope (variable_scope)
);

CREATE TABLE IF NOT EXISTS component_configs (
  version VARCHAR(32) NOT NULL,
  component VARCHAR(32) NOT NULL,
  instance VARCHAR(191) NOT NULL,
  name VARCHAR(255) NOT NULL,
  value MEDIUMTEXT,
  raw_row JSON,
  imported_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (version, component, instance, name),
  KEY idx_component_configs_component_name (component, name),
  KEY idx_component_configs_name (name)
);

CREATE TABLE IF NOT EXISTS config_item_metadata (
  content_type VARCHAR(64) NOT NULL,
  component VARCHAR(32) NOT NULL,
  item_key VARCHAR(255) NOT NULL,
  display_name VARCHAR(255) NOT NULL,
  value_type VARCHAR(64),
  variable_scope VARCHAR(191),
  source VARCHAR(64) NOT NULL,
  metadata JSON,
  imported_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (content_type, component, item_key),
  KEY idx_config_item_metadata_component (component, item_key)
);

CREATE TABLE IF NOT EXISTS raw_snapshots (
  version VARCHAR(32) NOT NULL,
  source VARCHAR(64) NOT NULL,
  path VARCHAR(512) NOT NULL,
  sha256 CHAR(64) NOT NULL,
  bytes BIGINT NOT NULL,
  payload_kind VARCHAR(16) NOT NULL,
  payload_json JSON,
  payload_text MEDIUMTEXT,
  imported_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (version, path),
  KEY idx_raw_snapshots_source (source),
  KEY idx_raw_snapshots_sha256 (sha256)
);
