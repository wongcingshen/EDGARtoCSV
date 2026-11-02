# EDGARtoCSV
Filter, reorganize, and convert JSON files from the SEC API into a customized format that is easier for system input.

## EDGAR → FactSet format (`EDGAR/EDGAR_to_FactSet.py`)

Turns SEC "company facts" JSON (`https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`)
into the **FactSet Fundamentals annual layout (FF_GEN_ANN_US)**: one row per company per fiscal year,
the same 91 pipe-delimited columns in the same order, money in thousands, EPS/dividends in cents,
`-` for missing values.

```bash
cd EDGAR

# Convert the JSON files in ./testJSON (latest fiscal year of each company)
python EDGAR_to_FactSet.py

# Download companies from the SEC first (SEC requires your name + e-mail as User-Agent)
python EDGAR_to_FactSet.py --tickers AAPL,MSFT,ABT --user-agent "Your Name you@example.com"

# All SEC filers, straight from the bulk file (no unzip needed):
#   https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip
python EDGAR_to_FactSet.py --zip companyfacts.zip --user-agent "Your Name you@example.com"
```

Useful options: `--years all` (or `--years 5`) for history, `--delimiter ,` for a CSV,
`--prefer restated` to use later restated figures instead of the originally reported ones.
Output goes to `EDGAR/Output/EDGAR_FactSet_Format.txt`.

### How the SEC items are categorised: `EDGAR/FactSet_Mapping.csv`

The SEC data has thousands of distinct tags. `FactSet_Mapping.csv` maps them onto the 91 FactSet
columns and can be edited without touching the code. Each column has a rule:

| Rule | Meaning |
|---|---|
| `Revenues \| SalesRevenueNet` | try tags left to right, first one found wins |
| `Goodwill + IntangibleAssetsNetExcludingGoodwill?` | add tags; `?` = optional (0 if missing) |
| `-PaymentsToAcquirePropertyPlantAndEquipment` | negate (FactSet shows cash outflows as negative) |
| `{Revenue} - {EBIT}` | use other FactSet columns |
| `ifrs-full:Revenue` | tag from a specific taxonomy (IFRS filers) |
| `@ticker`, `@fiscal_end`, ... | filled in by the script |

Rows with Section `Helper` (e.g. `Impairments`) are intermediate figures used by other rules
but not written to the output.

Main FactSet conventions built into the mapping (found by comparing with a real FactSet file):

- **Debt includes lease liabilities**: Long/Short Term Debt add operating-lease liabilities,
  and Fixed Assets include right-of-use assets.
- **EBIT excludes impairments**, and Total Operating Expense = Revenue − EBIT.
- **COGS including D&A** adds separately reported intangible amortization. For companies with
  no cost of sales at all, it holds D&A.
- **SG&A** is whatever operating expense is not COGS, so it includes R&D.
- **Accounts receivable** include contract assets (unbilled amounts).
- **Other Appropriated Reserves** = accumulated other comprehensive income excluding currency translation.
- **Funds from Operations** = Operating cash flow − Changes in working capital.
- **Fiscal year label**: years ending mid-January to May are labelled as the prior year (`2022-05-29` → `2021/FY`).

### Checking accuracy: `EDGAR/compare_factset.py`

```bash
python compare_factset.py FF_GEN_ANN_US_20230224.txt Output/EDGAR_FactSet_Format.txt --details
```

Matches rows on FactSet Ticker + Fiscal End and reports, per column, how many values agree
within 1%. Against the Feb-2023 FactSet file, 28 overlapping company-years match on about 75% of values:

- **≈90–100%**: revenue, profit before/after tax, net earnings, total equity, current assets/liabilities,
  cash, inventories, operating/investing cash flow, capex, dividends paid, net change in cash.
- **≈50–85%**: total assets/liabilities, debt, EPS, interest, COGS, EBIT/EBITDA.
- **Approximate**: SG&A, unusual items, changes in working capital, funds from operations,
  debt issuance. FactSet classifies these partly by hand, and SEC data has no equivalent flag.

### What cannot come from the SEC

- **FactSet Security ID and SEDOL** are proprietary. Put them in an optional `EDGAR/Identifiers.csv`
  (`CIK,FactSet Ticker,FactSet Security ID,Sedol,Exchange`) and they are filled in.
- **Ticker / Exchange** come from SEC's `company_tickers_exchange.json`, downloaded automatically
  when `--user-agent` is given.
- **Report Date** is the 10-K filing date. FactSet uses the earlier earnings-release date
  when it has one.
- **Data Update Indicator** is a FactSet code, written as `-`. **Proforma** is left blank.
- **Per-share figures** are not adjusted for later stock splits; FactSet adjusts them.
- **FactSet adjustments**: for some companies FactSet adds the same amount to total assets and
  total liabilities, apparently by grossing up netted items such as deferred taxes. The SEC figure
  is used as reported.

## Original wide-format extract (`EDGAR/EDGAR_Extract.py`)

Outputs every SEC tag as its own column (`EDGAR/Output/Sample.csv`). `Exclusion.txt` lists
tags to drop from that output.
