# EDGARtoCSV
Filter, reorganize, and convert JSON files from the SEC API into a customized format that is easier for system input.

## EDGAR → standardised fundamentals (`EDGAR/EDGAR_to_Fundamentals.py`)

Turns SEC "company facts" JSON (`https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`)
into a **standardised annual fundamentals layout**: one row per company per fiscal year,
91 fixed pipe-delimited columns (income statement, balance sheet, cash flow), money in thousands,
EPS/dividends in cents, `-` for missing values.

```bash
cd EDGAR

# Convert the JSON files in ./testJSON (latest fiscal year of each company)
python EDGAR_to_Fundamentals.py

# Download companies from the SEC first (SEC requires your name + e-mail as User-Agent)
python EDGAR_to_Fundamentals.py --tickers AAPL,MSFT,ABT --user-agent "Your Name you@example.com"

# All SEC filers, straight from the bulk file (no unzip needed):
#   https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip
python EDGAR_to_Fundamentals.py --zip companyfacts.zip --user-agent "Your Name you@example.com"
```

Useful options: `--years all` (or `--years 5`) for history, `--delimiter ,` for a CSV,
`--prefer restated` to use later restated figures instead of the originally reported ones.
Output goes to `EDGAR/Output/EDGAR_Fundamentals.txt`.

### How the SEC items are categorised: `EDGAR/Fundamentals_Mapping.csv`

The SEC data has thousands of distinct tags. `Fundamentals_Mapping.csv` maps them onto the 91
output columns and can be edited without touching the code. Each column has a rule:

| Rule | Meaning |
|---|---|
| `Revenues \| SalesRevenueNet` | try tags left to right, first one found wins |
| `Goodwill + IntangibleAssetsNetExcludingGoodwill?` | add tags; `?` = optional (0 if missing) |
| `-PaymentsToAcquirePropertyPlantAndEquipment` | negate (cash outflows are shown as negative) |
| `{Revenue} - {EBIT}` | use other output columns |
| `ifrs-full:Revenue` | tag from a specific taxonomy (IFRS filers) |
| `@ticker`, `@fiscal_end`, ... | filled in by the script |

Rows with Section `Helper` (e.g. `Impairments`) are intermediate figures used by other rules
but not written to the output.

Conventions built into the mapping:

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

### Checking accuracy: `EDGAR/compare_output.py`

```bash
python compare_output.py reference.txt Output/EDGAR_Fundamentals.txt --details
```

Compares the output with a reference file in the same layout. It matches rows on Ticker +
Fiscal End and reports, per column, how many values agree within 1%. Use it to tune the mapping.

Accuracy varies by column:

- **Most reliable**: revenue, profit before/after tax, net earnings, total equity, current assets/liabilities,
  cash, inventories, operating/investing cash flow, capex, dividends paid, net change in cash.
- **Approximate**: SG&A, unusual items, changes in working capital, funds from operations,
  debt issuance. How these are classified is a judgement call, and SEC data has no equivalent flag.

### What cannot come from the SEC

- **Security ID and SEDOL** are proprietary identifiers. Put them in an optional `EDGAR/Identifiers.csv`
  (`CIK,Ticker,Security ID,Sedol,Exchange`) and they are filled in.
- **Ticker / Exchange** come from SEC's `company_tickers_exchange.json`, downloaded automatically
  when `--user-agent` is given.
- **Report Date** is the 10-K filing date.
- **Data Update Indicator** is written as `-`, and **Proforma** is left blank.
- **Per-share figures** are not adjusted for later stock splits.

## Original wide-format extract (`EDGAR/EDGAR_Extract.py`)

Outputs every SEC tag as its own column (`EDGAR/Output/Sample.csv`). `Exclusion.txt` lists
tags to drop from that output.
