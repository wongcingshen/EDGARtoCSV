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
`--prefer restated` to use later restated figures instead of the originally reported ones,
`--currency native` to keep foreign companies in their own currency.

**Foreign companies** (20-F / 40-F filers) are converted to US dollars: balance-sheet figures at the
year-end rate, income-statement, cash-flow and per-share figures at the year's average rate
(European Central Bank rates from api.frankfurter.dev, cached in `fx_rates.json`). Currencies the
ECB doesn't publish stay in the reporting currency, shown in the Currency column.
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
- **Banks**: revenue = interest income + non-interest income; operating expense = non-interest expense
  + interest expense + credit-loss provision; EBIT is left empty.
- **Operating-lease payments count as financing**, not operating, cash flow (consistent with leases as debt).
- **IFRS filers** use the matching `ifrs-full` tags.
- **Fiscal year label**: years ending mid-January to May are labelled as the prior year (`2022-05-29` → `2021/FY`).

### Checking accuracy: `EDGAR/compare_output.py`

```bash
python compare_output.py reference.txt Output/EDGAR_Fundamentals.txt --details
```

Compares the output with a reference file in the same layout. It matches rows on Ticker +
Fiscal End and reports, per column, how many values agree within 1%. Use it to tune the mapping.

Tested on 256 randomly chosen companies (NYSE, NASDAQ and OTC, fiscal 2022), about 68% of all values
match within 1%:

| Match rate | Columns |
|---|---|
| **85–99%** | Profit before/after tax, net earnings, revenue, total assets, total equity, inventories, minority interest, net change in cash, FX effect |
| **70–84%** | EPS, dividends per share, total/current liabilities, current assets, cash, fixed assets, accounts payable, shares, book value per share, operating/investing/financing cash flow, capex, dividends paid |
| **50–69%** | Intangibles, receivables, debt, interest, D&A, taxes and interest paid, total operating expense |
| **Below 50%** | COGS, SG&A, EBIT/EBITDA, unusual items, working capital, funds from operations, debt issuance, other non-current assets |

Why the bottom rows are weak: these classifications are partly judgement calls (which charges are
"unusual", what counts as working capital), and the SEC data does not say where an item was presented.
For example, taxes or interest paid shown only in the notes are reported as 0 in the reference
layout but are still found in the SEC data.

### What cannot come from the SEC

- **Security ID and SEDOL** are proprietary identifiers. Put them in an optional `EDGAR/Identifiers.csv`
  (`CIK,Ticker,Security ID,Sedol,Exchange`) and they are filled in.
- **Ticker / Exchange** come from SEC's `company_tickers_exchange.json`, downloaded automatically
  when `--user-agent` is given.
- **Report Date** is the 10-K filing date.
- **Data Update Indicator** is written as `-`, and **Proforma** is left blank.
- **Per-share figures** are not adjusted for later stock splits, and for foreign companies they are
  per ordinary share, not per US depositary share (ADR).

## Original wide-format extract (`EDGAR/EDGAR_Extract.py`)

Outputs every SEC tag as its own column (`EDGAR/Output/Sample.csv`). `Exclusion.txt` lists
tags to drop from that output.
