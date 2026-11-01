"""Party shifts, volatility and round shifts on small hand-made elections."""

import duckdb
import pytest

from election_tally import results, shifts
from election_tally.load import create_schema

# (year, round, municipality code, zone, section, {candidate number: (votes, party)})
SECTIONS = [
    (2018, 1, 100, 1, 1, {15: (40, "PMDB"), 13: (60, "PT")}),
    (2018, 1, 100, 1, 2, {13: (30, "PT"), 45: (70, "PSDB")}),
    (2022, 1, 100, 1, 1, {15: (50, "MDB"), 13: (30, "PT"), 30: (20, "NOVO")}),
    (2022, 1, 100, 1, 2, {13: (50, "PT"), 45: (50, "PSDB")}),
    (2022, 1, 100, 1, 3, {13: (10, "PT")}),                       # section created in 2022
    (2018, 2, 100, 1, 1, {13: (55, "PT"), 45: (45, "PSDB")}),
    (2022, 2, 100, 1, 1, {13: (60, "PT"), 45: (40, "PSDB")}),    # section 2 has no round 2
    (2018, 1, 200, 1, 1, {25: (10, "DEM")}),                      # a merger is not a rename
    (2022, 1, 200, 1, 1, {44: (10, "UNIÃO")}),
]


def _db(tmp_path):
    path = tmp_path / "e.duckdb"
    con = duckdb.connect(str(path))
    create_schema(con)
    for year, rnd, mun, zone, section, votes in SECTIONS:
        for number, (count, party) in votes.items():
            con.execute("""INSERT INTO votes (election_year, round_number, state,
                           municipality_code, municipality_name, municipality_ibge_code, zone,
                           section, office_code, vote_type, candidate_number, candidate_name, party,
                           vote_count) VALUES (?, ?, 'SP', ?, ?, ?, ?, ?, 1, 'Nominal', ?, ?, ?, ?)""",
                        [year, rnd, mun, f"CIDADE {mun}", mun + 5000, zone, section, number,
                         f"C{number}", party, count])
    # Blanks and nulls are not valid votes: they must not enter the total.
    con.execute("""INSERT INTO votes (election_year, round_number, state, municipality_code, zone,
                   section, office_code, vote_type, candidate_number, candidate_name, vote_count)
                   VALUES (2018, 1, 'SP', 100, 1, 1, 1, 'Branco', 95, 'BRANCO', 999)""")
    con.close()
    results.build(path)
    shifts.build(path)
    return path


def _rows(path, sql, *args):
    con = duckdb.connect(str(path), read_only=True)
    try:
        return con.execute(sql, list(args)).fetchall()
    finally:
        con.close()


def _shift(path, level, transition, **where):
    clause = " AND ".join(f"{k} = ?" for k in where)
    rows = _rows(path, f"""SELECT party, votes_from, votes_to, share_from, share_to, share_change
                           FROM party_shifts WHERE level = ? AND transition = ?
                           {'AND ' + clause if clause else ''} ORDER BY party""",
                 level, transition, *where.values())
    return {r[0]: r[1:] for r in rows}


def test_renamed_party_is_one_party_and_new_party_is_zero_filled(tmp_path):
    path = _db(tmp_path)
    got = _shift(path, "Seção", "2018 → 2022 T1",
                 municipality_code=100, zone=1, section=1)
    assert set(got) == {"MDB", "NOVO", "PT"}  # PMDB is MDB; NOVO did not run in 2018
    assert got["NOVO"][:2] == (0, 20) and got["NOVO"][2:4] == (0.0, pytest.approx(0.2))
    assert got["MDB"][0] == 40 and got["MDB"][1] == 50


def test_share_is_over_valid_votes_and_change_is_the_difference(tmp_path):
    path = _db(tmp_path)
    got = _shift(path, "Seção", "2018 → 2022 T1", municipality_code=100, zone=1, section=1)
    # the 999 blanks of 2018 are not in the total of 100
    assert got["PT"][2:] == (pytest.approx(0.6), pytest.approx(0.3), pytest.approx(-0.3))
    assert sum(v[2] for v in got.values()) == pytest.approx(1)
    assert sum(v[3] for v in got.values()) == pytest.approx(1)


def test_unit_must_exist_on_both_sides(tmp_path):
    path = _db(tmp_path)
    sections = _rows(path, """SELECT DISTINCT section FROM party_shifts
                              WHERE level = 'Seção' AND municipality_code = 100
                              AND transition = '2018 → 2022 T1'""")
    assert sorted(s[0] for s in sections) == [1, 2]            # section 3 is new
    round2 = _rows(path, """SELECT DISTINCT section FROM party_shifts
                            WHERE level = 'Seção' AND transition = '2018 → 2022 T2'
                            AND municipality_code = 100""")
    assert round2 == [(1,)]


def test_merged_parties_are_not_joined(tmp_path):
    path = _db(tmp_path)
    got = _shift(path, "Município", "2018 → 2022 T1", municipality_code=200)
    assert got["DEM"][:4] == (10, 0, 1.0, 0.0)
    assert got["UNIÃO"][:4] == (0, 10, 0.0, 1.0)


def test_pedersen_index(tmp_path):
    path = _db(tmp_path)
    rows = _rows(path, """SELECT section, pedersen_index FROM unit_volatility
                          WHERE level = 'Seção' AND transition = '2018 → 2022 T1'
                          AND municipality_code = 100 ORDER BY section""")
    # section 1: 0.5 * (|.5-.4| + |.3-.6| + |.2-0|); section 2: 0.5 * (.2 + .2)
    assert rows == [(1, pytest.approx(0.3)), (2, pytest.approx(0.2))]
    # volatility of a unit is half the sum of its changes in party_shifts
    check = _rows(path, """SELECT count(*) FROM (
        SELECT v.pedersen_index, 0.5 * sum(abs(s.share_change)) AS p
        FROM unit_volatility v JOIN party_shifts s USING (level, office_code, round_number,
             transition, municipality_code, zone, section)
        WHERE level = 'Seção' GROUP BY ALL) WHERE abs(pedersen_index - p) > 1e-9""")
    assert check == [(0,)]


def test_levels_and_unit_columns(tmp_path):
    path = _db(tmp_path)
    uf = _rows(path, """SELECT state, state_iso, municipality_code, zone FROM party_shifts
                        WHERE level = 'UF' AND transition = '2018 → 2022 T1' LIMIT 1""")
    assert uf == [("SP", "BR-SP", None, None)]
    zone = _rows(path, """SELECT state, zone, municipality_code, section FROM party_shifts
                          WHERE level = 'Zona' LIMIT 1""")
    assert zone == [("SP", 1, None, None)]
    section = _rows(path, """SELECT state, municipality_name, municipality_ibge_code, zone, section
                             FROM party_shifts WHERE level = 'Seção' AND municipality_code = 100
                             AND section = 2 LIMIT 1""")
    assert section == [("SP", "CIDADE 100", 5100, 1, 2)]
    brasil = _shift(path, "Brasil", "2018 → 2022 T1")
    assert brasil["PT"][:2] == (90, 90)
    assert _rows(path, """SELECT DISTINCT state FROM party_shifts WHERE level = 'Brasil'""") == [(None,)]


def test_share_of_a_municipality_sums_its_sections(tmp_path):
    path = _db(tmp_path)
    got = _shift(path, "Município", "2018 → 2022 T1", municipality_code=100)
    # 2018: PT 90 of 200; 2022: PT 90 of 210 (the new section 3 counts at this level)
    assert got["PT"][2:4] == (pytest.approx(0.45), pytest.approx(90 / 210))


def test_up_to_date_tables_are_not_rebuilt(tmp_path, capsys):
    path = _db(tmp_path)
    capsys.readouterr()
    shifts.build(path)
    assert "up to date" in capsys.readouterr().out
    con = duckdb.connect(str(path))
    con.execute("UPDATE votes SET vote_count = vote_count + 1 WHERE candidate_number = 13")
    con.close()
    results.build(path)
    capsys.readouterr()
    shifts.build(path)
    assert "up to date" not in capsys.readouterr().out


def test_round_shifts_follow_finalists_between_rounds(tmp_path):
    path = tmp_path / "r.duckdb"
    con = duckdb.connect(str(path))
    create_schema(con)
    # municipality -> {round: {candidate: (votes, party)}}
    data = {100: {1: {13: (50, "PT"), 15: (30, "PMDB"), 12: (20, "PDT")},
                  2: {13: (60, "PT"), 15: (40, "PMDB")}},
            200: {1: {13: (10, "PT")}, 2: {13: (5, "PT"), 15: (5, "PMDB")}},  # 15 had no votes
            300: {1: {13: (9, "PT")}}}                                            # one round only
    for mun, rounds in data.items():
        for rnd, votes in rounds.items():
            for number, (count, party) in votes.items():
                con.execute("""INSERT INTO votes (election_year, round_number, state,
                               municipality_code, municipality_name, zone, section, office_code,
                               vote_type, candidate_number, candidate_name, party, vote_count)
                               VALUES (2022, ?, 'SP', ?, ?, 1, 1, 1, 'Nominal', ?, ?, ?, ?)""",
                            [rnd, mun, f"CIDADE {mun}", number, f"C{number}", party, count])
    con.close()
    results.build(path)
    shifts.build(path)
    rows = _rows(path, """SELECT municipality_code, candidate_number, party, votes_round1,
                          votes_round2, share_round1, share_round2, share_change, votes_change
                          FROM round_shifts ORDER BY 1, 2""")
    assert [r[:2] for r in rows] == [(100, 13), (100, 15), (200, 13), (200, 15)]  # 12 and 300 out
    assert rows[0][2:5] == ("PT", 50, 60)
    assert rows[1][2] == "MDB"                                   # the party under its current name
    assert rows[0][5:] == (pytest.approx(0.5), pytest.approx(0.6), pytest.approx(0.1), 10)
    assert rows[3][3:5] == (0, 5)                                # no votes in round 1: 0, not missing
    assert rows[3][5:8] == (0.0, pytest.approx(0.5), pytest.approx(0.5))


def test_state_round_shifts_use_the_state_totals(tmp_path):
    path = tmp_path / "s.duckdb"
    con = duckdb.connect(str(path))
    create_schema(con)
    # (state, municipality, round, candidate, party, votes)
    rows = [("SP", 100, 1, 13, "PT", 90), ("SP", 100, 1, 12, "PDT", 10),  # big city
            ("SP", 100, 2, 13, "PT", 80), ("SP", 100, 2, 15, "PMDB", 20),
            ("SP", 200, 1, 15, "PMDB", 10),                               # small city, 13 had no votes
            ("SP", 200, 2, 13, "PT", 5), ("SP", 200, 2, 15, "PMDB", 5),
            ("RJ", 300, 1, 13, "PT", 9),                                  # state with one round only
            ("AM", 400, 1, 13, "PT", 10), ("AM", 400, 2, 15, "PMDB", 10)]  # each finalist in one round
    for state, mun, rnd, number, party, count in rows:
        con.execute("""INSERT INTO votes (election_year, round_number, state, municipality_code,
                       municipality_name, zone, section, office_code, vote_type, candidate_number,
                       candidate_name, party, vote_count)
                       VALUES (2022, ?, ?, ?, ?, 1, 1, 1, 'Nominal', ?, ?, ?, ?)""",
                    [rnd, state, mun, f"CIDADE {mun}", number, f"C{number}", party, count])
    con.close()
    results.build(path)
    shifts.build(path)
    got = {r[:2]: r[2:] for r in _rows(path, """SELECT state, candidate_number, party, votes_round1,
           votes_round2, share_round1, share_round2, share_change, votes_change
           FROM state_round_shifts""")}
    assert set(got) == {("SP", 13), ("SP", 15), ("AM", 13), ("AM", 15)}  # 12 and RJ are out
    # SP round 1: 13 has 90 of 110 votes; the mean of its cities' shares would be 45%
    assert got["SP", 13] == ("PT", 90, 85, pytest.approx(90 / 110), pytest.approx(85 / 110),
                             pytest.approx(85 / 110 - 90 / 110), -5)
    assert got["SP", 15][:3] == ("MDB", 10, 25)                  # the party under its current name
    assert got["AM", 15][1:5] == (0, 10, 0.0, 1.0)               # no votes in a round: 0, not missing


def test_zone_round_shifts_keep_equal_zone_numbers_of_two_states_apart(tmp_path):
    path = tmp_path / "z.duckdb"
    con = duckdb.connect(str(path))
    create_schema(con)
    # (state, zone, section, round, vote type, candidate, party, votes)
    rows = [("SP", 1, 1, 1, "Nominal", 13, "PT", 60), ("SP", 1, 2, 1, "Nominal", 13, "PT", 20),
            ("SP", 1, 1, 1, "Nominal", 12, "PDT", 20),
            ("SP", 1, 1, 1, "Branco", 95, None, 500),               # not a valid vote
            ("SP", 1, 1, 2, "Nominal", 13, "PT", 75), ("SP", 1, 1, 2, "Nominal", 15, "PMDB", 25),
            ("RJ", 1, 1, 1, "Nominal", 15, "PMDB", 10),             # same number, other state
            ("RJ", 1, 1, 2, "Nominal", 15, "PMDB", 8), ("RJ", 1, 1, 2, "Nominal", 13, "PT", 2),
            ("RJ", 2, 1, 1, "Nominal", 13, "PT", 9)]                # one round only
    for state, zone, section, rnd, kind, number, party, count in rows:
        con.execute("""INSERT INTO votes (election_year, round_number, state, municipality_code,
                       municipality_name, zone, section, office_code, vote_type, candidate_number,
                       candidate_name, party, vote_count)
                       VALUES (2022, ?, ?, 1, 'X', ?, ?, 1, ?, ?, ?, ?, ?)""",
                    [rnd, state, zone, section, kind, number, f"C{number}", party, count])
    con.close()
    results.build(path)
    shifts.build(path)
    got = {r[:3]: r[3:] for r in _rows(path, """SELECT state, zone, candidate_number, party, votes_round1,
           votes_round2, share_round1, share_round2, votes_change FROM zone_round_shifts""")}
    assert set(got) == {("SP", 1, 13), ("SP", 1, 15), ("RJ", 1, 13), ("RJ", 1, 15)}
    assert got["SP", 1, 13] == ("PT", 80, 75, pytest.approx(0.8), pytest.approx(0.75), -5)
    assert got["RJ", 1, 13][1:] == (0, 2, 0.0, pytest.approx(0.2), 2)   # no votes in a round: 0
    assert got["SP", 1, 15][0] == "MDB"


def test_missing_round_table_is_built_without_redoing_party_shifts(tmp_path, capsys):
    path = _db(tmp_path)
    con = duckdb.connect(str(path))
    con.execute("DROP TABLE zone_round_shifts")
    con.close()
    capsys.readouterr()
    shifts.build(path)
    out = capsys.readouterr().out
    assert "zone_round_shifts:" in out and "party_shifts:" not in out
    capsys.readouterr()
    shifts.build(path)
    assert "up to date" in capsys.readouterr().out


def test_a_change_in_the_renames_file_rebuilds(tmp_path, monkeypatch, capsys):
    from election_tally import parties
    path = _db(tmp_path)
    csv = tmp_path / "renames.csv"
    csv.write_text("old_name,current_name,year_of_change,note\nPSDB,TUCANOS,2020,x\n", encoding="utf-8")
    monkeypatch.setattr(parties, "RENAMES_PATH", csv)
    capsys.readouterr()
    shifts.build(path)
    assert "up to date" not in capsys.readouterr().out
    got = _shift(path, "Município", "2018 → 2022 T1", municipality_code=100)
    assert "TUCANOS" in got and "PSDB" not in got and "PMDB" in got  # only this file counts
