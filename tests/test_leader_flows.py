"""Sections and municipalities moving between most-voted candidates, on small hand-made rounds."""

import duckdb

from election_tally import leader_flows as lf
from election_tally.load import create_schema

# (year, round, municipality, section, {number: votes})
ROUNDS = [
    (2018, 1, 100, 1, {13: 50, 17: 30}),
    (2018, 2, 100, 1, {13: 40, 17: 45}),   # changes leader
    (2018, 1, 100, 2, {13: 20, 17: 20}),   # tie
    (2018, 2, 100, 2, {13: 20, 17: 10}),
    (2018, 1, 100, 3, {}),                 # nobody voted
    (2018, 2, 100, 3, {17: 5}),
    (2018, 2, 100, 4, {13: 9}),            # closes before the next election
    (2022, 1, 100, 1, {13: 60, 17: 30}),
    (2022, 1, 200, 5, {17: 8}),            # did not exist before
]
NAMES = {13: "LULA", 17: "JAIR"}


def _db(tmp_path):
    path = tmp_path / "e.duckdb"
    con = duckdb.connect(str(path))
    create_schema(con)
    for y, r, m, s, votes in ROUNDS:
        con.execute("""INSERT INTO sections (election_year, round_number, state, municipality_code,
                       municipality_name, zone, section, office_code)
                       VALUES (?, ?, 'SP', ?, 'CIDADE', 1, ?, 1)""", [y, r, m, s])
        for num, v in votes.items():
            con.execute("""INSERT INTO votes (election_year, round_number, state, municipality_code,
                           zone, section, office_code, vote_type, candidate_number, candidate_name,
                           vote_count) VALUES (?, ?, 'SP', ?, 1, ?, 1, 'Nominal', ?, ?, ?)""",
                        [y, r, m, s, num, NAMES[num], v])
    # Blanks never lead, however many.
    con.execute("""INSERT INTO votes (election_year, round_number, state, municipality_code, zone,
                   section, office_code, vote_type, candidate_number, candidate_name, vote_count)
                   VALUES (2018, 1, 'SP', 100, 1, 1, 1, 'Branco', 95, 'BRANCO', 999)""")
    con.close()
    return path


def _links(path, level):
    con = duckdb.connect(str(path), read_only=True)
    rows = con.execute("""SELECT from_year, from_round, source_candidate, target_candidate,
                                 sum(units)::INTEGER FROM leader_flows
                          WHERE level = ? GROUP BY ALL""", [level]).fetchall()
    con.close()
    return {tuple(r[:4]): r[4] for r in rows}


def test_counts_sections_by_most_voted_candidate(tmp_path):
    path = _db(tmp_path)
    lf.build(path)
    assert _links(path, lf.SECTION) == {
        (2018, 1, "LULA", "JAIR"): 1,
        (2018, 1, lf.TIE, "LULA"): 1,
        (2018, 1, lf.NO_VOTES, "JAIR"): 1,
        (2018, 1, "Seção nova", "LULA"): 1,
        (2018, 2, "JAIR", "LULA"): 1,
        (2018, 2, "LULA", "Seção encerrada"): 2,
        (2018, 2, "JAIR", "Seção encerrada"): 1,
        (2018, 2, "Seção nova", "JAIR"): 1,
    }


def test_municipality_leader_sums_its_sections(tmp_path):
    path = _db(tmp_path)
    lf.build(path)
    # 100: 70x50 for 13, then 69x60 for 13, then 60x30 for 13. 200 appears only in 2022.
    assert _links(path, lf.MUNICIPALITY) == {
        (2018, 1, "LULA", "LULA"): 1,
        (2018, 2, "LULA", "LULA"): 1,
        (2018, 2, "Município novo", "JAIR"): 1,
    }


def test_up_to_date_table_is_not_rebuilt(tmp_path):
    path = _db(tmp_path)
    assert lf.build(path) > 0
    assert lf.build(path) is None
    con = duckdb.connect(str(path))
    con.execute("UPDATE votes SET vote_count = vote_count + 1 WHERE candidate_number = 13")
    con.close()
    assert lf.build(path) > 0
