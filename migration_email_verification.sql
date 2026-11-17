USE siap_db;

ALTER TABLE users
  ADD COLUMN email_verified TINYINT(1) NOT NULL DEFAULT 1,
  ADD COLUMN verification_code_salt CHAR(32) NULL,
  ADD COLUMN verification_code_hash CHAR(64) NULL,
  ADD COLUMN verification_expires_at DATETIME NULL,
  ADD COLUMN verification_sent_at DATETIME NULL,
  ADD COLUMN verification_attempts TINYINT UNSIGNED NOT NULL DEFAULT 0;
