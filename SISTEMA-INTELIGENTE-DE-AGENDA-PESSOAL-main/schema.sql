CREATE DATABASE IF NOT EXISTS siap_db
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE siap_db;

CREATE TABLE IF NOT EXISTS users (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  name VARCHAR(120) NOT NULL,
  email VARCHAR(190) NOT NULL,
  phone VARCHAR(20) NULL,
  sms_alerts TINYINT(1) NOT NULL DEFAULT 0,
  avatar_data MEDIUMTEXT NULL,
  password_salt CHAR(32) NOT NULL,
  password_hash CHAR(64) NOT NULL,
  email_verified TINYINT(1) NOT NULL DEFAULT 1,
  verification_code_salt CHAR(32) NULL,
  verification_code_hash CHAR(64) NULL,
  verification_expires_at DATETIME NULL,
  verification_sent_at DATETIME NULL,
  verification_attempts TINYINT UNSIGNED NOT NULL DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_users_email (email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS tasks (
  user_id BIGINT UNSIGNED NOT NULL,
  id VARCHAR(80) NOT NULL,
  title VARCHAR(255) NOT NULL,
  task_date DATE NOT NULL,
  task_time TIME NULL,
  category VARCHAR(80) NOT NULL DEFAULT 'Pessoal',
  done TINYINT(1) NOT NULL DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (user_id, id),
  KEY idx_tasks_user_date (user_id, task_date, task_time),
  CONSTRAINT fk_tasks_user FOREIGN KEY (user_id)
    REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS sessions (
  token_hash CHAR(64) NOT NULL,
  user_id BIGINT UNSIGNED NOT NULL,
  expires_at DATETIME NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (token_hash),
  KEY idx_sessions_expiry (expires_at),
  CONSTRAINT fk_sessions_user FOREIGN KEY (user_id)
    REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
