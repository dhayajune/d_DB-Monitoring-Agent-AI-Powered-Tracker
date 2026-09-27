-- Wind turbine installation tracking database.
-- Each row is one installation event: WHICH turbine, at WHICH site/location, WHICH phase, WHO worked on it,
-- WHEN, the resulting STATUS, and any ISSUE found on site plus the FIX applied.

CREATE DATABASE IF NOT EXISTS workflow_db
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

USE workflow_db;

CREATE TABLE IF NOT EXISTS workflow_details (
  id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  turbine_id          VARCHAR(30)  NOT NULL COMMENT 'Turbine tag, e.g. KYR-WTG-07',
  site_name           VARCHAR(120) NOT NULL COMMENT 'Wind farm',
  location            VARCHAR(160) NOT NULL COMMENT 'District / region / country',
  turbine_model       VARCHAR(60)  NOT NULL,
  installation_phase  VARCHAR(60)  NOT NULL COMMENT 'Foundation, Tower Erection, Nacelle Installation, Rotor & Blade Installation, Electrical & Grid Connection, Commissioning',
  technician          VARCHAR(100) NOT NULL,
  status              ENUM('OPEN','PROCESSING','COMPLETED') NOT NULL,
  notes               VARCHAR(500) NULL COMMENT 'Work carried out',
  issue               VARCHAR(500) NULL COMMENT 'Issue found on site, if any',
  fix_applied         VARCHAR(500) NULL COMMENT 'How the issue was fixed (NULL while still being worked on)',
  processed_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'When the work was recorded',
  created_at          TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'When the row was inserted',
  PRIMARY KEY (id),
  KEY idx_turbine (turbine_id),
  KEY idx_site (site_name),
  KEY idx_status_time (status, processed_at)
) ENGINE=InnoDB;

-- Sample data (only inserted when the table is empty).
INSERT INTO workflow_details
  (turbine_id, site_name, location, turbine_model, installation_phase, technician, status, notes, issue, fix_applied, processed_at)
SELECT * FROM (
  SELECT 'KYR-WTG-07' AS turbine_id, 'Kayathar Ridge Wind Park' AS site_name, 'Thoothukudi, Tamil Nadu, India' AS location,
         '5.7 MW - 163 m rotor' AS turbine_model, 'Foundation' AS installation_phase, 'arjun.v' AS technician,
         'OPEN' AS status, 'Anchor cage installed and surveyed' AS notes, NULL AS issue, NULL AS fix_applied,
         NOW() - INTERVAL 30 HOUR AS processed_at
  UNION ALL SELECT 'KYR-WTG-07', 'Kayathar Ridge Wind Park', 'Thoothukudi, Tamil Nadu, India', '5.7 MW - 163 m rotor',
         'Tower Erection', 'karthik.s', 'PROCESSING', 'Tower sections 1-3 erected',
         'Flange gap above tolerance on section 2', 'Shimmed and re-torqued flange bolts', NOW() - INTERVAL 6 HOUR
  UNION ALL SELECT 'EMN-WTG-12', 'Emsland North Wind Farm', 'Lower Saxony, Germany', '4.8 MW - 149 m rotor',
         'Foundation', 'lena.m', 'OPEN', 'Foundation concrete pour completed', NULL, NULL, NOW() - INTERVAL 3 HOUR
) AS seed
WHERE NOT EXISTS (SELECT 1 FROM workflow_details);
