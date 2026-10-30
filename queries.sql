-- Query examples (DuckDB / Superset SQL Lab).
-- Example: president, 1st round of 2022 (election_code = 544, office_code = 1).
-- To change level, change the columns in PARTITION BY / GROUP BY:
--   Brazil:               (nothing)
--   state:                state
--   municipality:         state, municipality_code, municipality_name
--   zone:                 state, zone            -- a zone can span several municipalities
--   municipality × zone:  state, municipality_code, zone

-- 1) Evolution: cumulative votes per candidate over the receipt of BUs, by zone
SELECT state, zone, candidate_number, candidate_name, bu_received_at,
       sum(sum(vote_count)) OVER (
           PARTITION BY state, zone, candidate_number
           ORDER BY bu_received_at) AS cumulative_votes
FROM votes
WHERE election_code = 544 AND office_code = 1
GROUP BY state, zone, candidate_number, candidate_name, bu_received_at
ORDER BY state, zone, candidate_number, bu_received_at;

-- 2) Evolution of the % of sections received, by municipality
SELECT state, municipality_code, municipality_name, bu_received_at,
       sum(count(*)) OVER w_time                          AS sections_received,
       sum(count(*)) OVER w_time
         / sum(count(*)) OVER w_total * 100               AS pct_sections,
       sum(sum(turnout)) OVER w_time                      AS cumulative_turnout
FROM sections
WHERE election_code = 544 AND office_code = 1
GROUP BY state, municipality_code, municipality_name, bu_received_at
WINDOW w_time  AS (PARTITION BY state, municipality_code ORDER BY bu_received_at),
       w_total AS (PARTITION BY state, municipality_code)
ORDER BY state, municipality_code, bu_received_at;

-- 3) Consolidated (final result) by state
SELECT state, candidate_number, candidate_name, sum(vote_count) AS total_votes,
       sum(vote_count) * 100.0 / sum(sum(vote_count)) OVER (PARTITION BY state) AS pct_total
FROM votes
WHERE election_code = 544 AND office_code = 1
GROUP BY state, candidate_number, candidate_name
ORDER BY state, total_votes DESC;

-- 4) Consolidated turnout by zone
SELECT state, zone, count(*) AS section_count, sum(eligible_voters) AS eligible,
       sum(turnout) AS turnout_total,
       sum(turnout) * 100.0 / sum(eligible_voters) AS pct_turnout
FROM sections
WHERE election_code = 544 AND office_code = 1
GROUP BY state, zone
ORDER BY state, zone;
