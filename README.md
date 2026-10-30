# apuracaoeleicoes

Rebuilds the **vote-count evolution** of Brazilian elections (*evolução da
apuração*) from the polling-station ballot reports (*boletins de urna*, BU)
published by the TSE, broken down by country, state (UF), municipality and
electoral zone. Everything is stored in a DuckDB file that is ready for Superset.

The idea: each BU carries the time at which it was received by the Electoral
Justice. Sorting the sections by that time and summing cumulatively gives the
vote-count curve at any level of aggregation.

## Supported years

| Year | File | Evolution? | Notes |
|------|------|------------|-------|
| 2016 | TXT without header | yes | round comes from the file name; receipt time in local time (converted) |
| 2018 | CSV | **no** | the TSE did not publish `DT_BU_RECEBIDO` or `DT_EMISSAO_BU`; only the consolidated totals |
| 2020 | CSV | yes | some names with badly escaped quotes (handled) |
| 2022 | CSV | yes | same layout as 2020 |
| 2024 | CSV | yes | dates in ISO format, numbers without quotes |
| 2026 | CSV | yes | same layout as 2024 |

2014 and earlier: the portal does not publish ballot reports in this format.

## Usage

```bash
pip install -e .            # or: pip install duckdb requests

python -m election_tally pipeline --year 2024                  # downloads and loads the whole year
python -m election_tally pipeline --year 2022 --state SP,RJ --round 1 --offices 1,3
```

Individual commands:

```bash
python -m election_tally download --year 2022 --state AC          # download only (checks the SHA-512)
python -m election_tally load --year 2022 data/raw/2022/*.zip
python -m election_tally inspect --year 2018 file.zip             # column mapping of the file
python -m election_tally validate --cycle ele2022 --election 544 --office 1   # sum of BUs x official
```

- Reloading a year/round/state replaces what was there before; different years coexist in the same database.
- `--offices` greatly reduces the size of the database (federal and state deputies and city councillors make up most of the rows).
- `validate` compares the sum of the ballot reports with the official JSON from the results release (`resultados.tse.jus.br`); use `--json` for a file that has already been downloaded.

## Data model

Two tables, with no cumulative totals stored:

- **`votes`**: one row per section × office × votable item (`vote_count`, `bu_received_at`, ...).
- **`sections`**: one row per section × office (`eligible_voters`, `turnout`, ballot box times).
  It is kept separate because these numbers would otherwise repeat on every row of `votes`.

Points of attention:

- **Times in Brasília.** Opening, closing and issuing times come in the ballot box's
  local time; the receipt time comes in Brasília time (2020+) or local time (2016).
  On load, everything is converted to Brasília time using the state's timezone
  (daylight saving time included). Simplification: western Amazonas and Fernando de
  Noronha use the state's main timezone, and abroad (`state = 'ZZ'`) is not converted.
- **Zone ≠ municipality subdivision.** A zone can cover several municipalities, and a
  municipality can have several zones. Group zones by `(state, zone)`.
- **Abroad** (`state = 'ZZ'`) counts toward the Brazil total and toward no state.
- **Uninstalled sections** appear with 0 votes and a null `candidate_number`.
- Receipt ≠ publication: the curve reflects BUs arriving, not the exact curve shown on the TSE website.

Examples for each level (evolution and consolidated) are in [`queries.sql`](queries.sql).

## Superset

```bash
pip install duckdb-engine
```

Connection (Settings → Database Connections → Other):

```
duckdb:////absolute/path/elections.duckdb?access_mode=read_only
```

Open it **read-only**: DuckDB allows only one process to write, and Superset holds
the connection open. To reload data, stop Superset (or build a new file and swap it in).

For the evolution curve: a time-series chart on `votes`, time axis `bu_received_at`
(minute granularity), metric `SUM(vote_count)`, dimension `candidate_name`, filters
on election year, office, state, municipality and zone, and, under *Advanced
Analytics*, the **cumulative sum** option. This way the same chart works at any level.

## How to support a new year (or a changed layout)

Each year uses a `Format` (`election_tally/formats.py`), which defines where the
files are, how to read them, and which raw column each column of the canonical
schema comes from. A column with no match becomes `NULL`; a missing required column
makes the load fail.

1. Run `python -m election_tally inspect --year YYYY file.zip` on a file from that year.
2. If only the name of a column changed, add the alias to the `mapping` of `BwebCSV`.
3. If the way of reading changed (separator, header, timezone, column derivation),
   create a subclass that overrides only the difference and register the year:

```python
@register(2028)
class Bweb2028(BwebCSV):
    delim = "|"
    mapping = {**BwebCSV.mapping, "section": ["NUM_SECAO"]}
```

Years without their own format inherit the format of the most recent registered
year before them. A new column in the schema goes into `CANONICAL`; existing tables
gain the column with `NULL` for the older years.

## Tests

```bash
pip install pytest && python -m pytest
```

The tests use synthetic data and real samples (AC, 1st round) from 2016, 2018, 2020
and 2024 in `tests/data/`.
