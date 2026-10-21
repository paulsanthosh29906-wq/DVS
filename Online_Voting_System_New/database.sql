CREATE DATABASE IF NOT EXISTS online_voting;
USE online_voting;

CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    voter_id VARCHAR(20) NOT NULL UNIQUE,
    email VARCHAR(150) NOT NULL UNIQUE,
    phone VARCHAR(15) NULL UNIQUE,
    has_voted BOOLEAN NOT NULL DEFAULT 0,
    password VARCHAR(255) NOT NULL,
    role ENUM('voter', 'admin') NOT NULL DEFAULT 'voter'
);

CREATE TABLE IF NOT EXISTS candidates (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    party VARCHAR(100) NOT NULL
);

CREATE TABLE IF NOT EXISTS election_settings (
    id INT PRIMARY KEY,
    start_at DATETIME NOT NULL,
    end_at DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS elections (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    start_at DATETIME NOT NULL,
    end_at DATETIME NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO election_settings (id, start_at, end_at)
VALUES (1, '2026-01-01 09:00:00', '2026-12-31 17:00:00')
ON DUPLICATE KEY UPDATE id = id;

INSERT INTO elections (id, name, start_at, end_at)
SELECT 1, 'Election 1', start_at, end_at
FROM election_settings
WHERE id = 1
  AND NOT EXISTS (SELECT 1 FROM elections WHERE id = 1);

CREATE TABLE IF NOT EXISTS votes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    voter_id INT NOT NULL,
    election_id INT NOT NULL,
    candidate_id INT NOT NULL,
    voted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_votes_voter_election UNIQUE (voter_id, election_id),
    CONSTRAINT fk_votes_voter FOREIGN KEY (voter_id) REFERENCES users(id) ON DELETE RESTRICT,
    CONSTRAINT fk_votes_election FOREIGN KEY (election_id) REFERENCES elections(id) ON DELETE RESTRICT,
    CONSTRAINT fk_votes_candidate FOREIGN KEY (candidate_id) REFERENCES candidates(id) ON DELETE RESTRICT
);

INSERT INTO candidates (name, party)
SELECT 'Candidate 1', 'Party A'
WHERE NOT EXISTS (SELECT 1 FROM candidates WHERE name = 'Candidate 1');

INSERT INTO candidates (name, party)
SELECT 'Candidate 2', 'Party B'
WHERE NOT EXISTS (SELECT 1 FROM candidates WHERE name = 'Candidate 2');

-- Admin account is created automatically by app.py on first successful start.
-- Demo Admin ID: ADMIN001
-- Demo Admin Password: admin123

-- IMPORTANT DESIGN: a voter can vote once PER ELECTION, not once for their lifetime.
-- When a closed election is followed by a new voting period, app.py creates a new
-- election row. Existing registered voters can then vote once in that new round.
