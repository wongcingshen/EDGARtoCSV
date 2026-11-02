"""
EDGAR_to_Fundamentals.py

Convert SEC EDGAR "company facts" JSON (https://data.sec.gov/api/xbrl/companyfacts/)
into a standardised annual fundamentals layout: 91 pipe-delimited columns,
money in thousands, EPS / dividends per share in cents.

Which SEC tags feed which output column is defined in Fundamentals_Mapping.csv, which you
can edit without touching this code. Rule syntax (column "Rule"):

    Alternatives are separated by "|" and tried left to right; the first that works wins.
    Inside an alternative, terms are combined with " + ", " - ", " * ", " / ".
      TagName              a us-gaap tag (also searched in ifrs-full, dei)
      ns:TagName           a tag from a specific namespace, e.g. ifrs-full:Revenue
      {Column}     another column of the output (computed first)
      123                  a constant
    A term ending in "?" is optional (counts as 0 when missing). Every other term must
    exist for the alternative to be used. If all terms are optional, at least one must exist.
    A leading "-" negates a term, e.g. "-PaymentsToAcquirePropertyPlantAndEquipment".
    Rules starting with "@" are filled in by the script (identifiers, dates, ...).
    Rows whose Section is "Helper" are intermediate figures: they can be referenced with
    {Name} but are not written to the output.

Examples
    # convert local JSON files (default: ./testJSON) for each company's latest year
    python EDGAR_to_Fundamentals.py

    # download companies first (SEC requires a User-Agent with your name and e-mail)
    python EDGAR_to_Fundamentals.py --tickers AAPL,MSFT,ABT --user-agent "Jane Doe jane@example.com"

    # every company, straight from SEC's bulk file (no need to unzip)
    #   https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip
    python EDGAR_to_Fundamentals.py --zip companyfacts.zip --years all
"""

import argparse
import csv
import datetime as dt
import io
import json
import os
import re
import sys
import time
import urllib.request
import zipfile
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))

ANNUAL_FORMS = {"10-K", "10-K/A", "10-KT", "10-KT/A", "20-F", "20-F/A", "40-F", "40-F/A"}
NAMESPACES = ("us-gaap", "ifrs-full", "dei", "srt", "invest")
MONEY_SCALE = 1000          # money is reported in thousands
SHARE_SCALE = 1000          # ... and share counts in thousands
PER_SHARE_SCALE = 0.01      # ... and per-share amounts in cents

EXCHANGE_NAMES = {"NYSE": "NYSE", "Nasdaq": "NASDAQ", "OTC": "US OTC", "CBOE": "Cboe BZX",
                    "NYSE American": "NYSE American", "NYSE Arca": "NYSE Arca"}

SEC_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"


# --------------------------------------------------------------------------- mapping rules

class Rule:
    """One column of Fundamentals_Mapping.csv, parsed into alternatives of terms."""

    TERM_RE = re.compile(r"^(?P<neg>-)?(?P<name>.+?)(?P<opt>\?)?$")

    def __init__(self, no, column, text, section=""):
        self.no = no
        self.column = column
        self.helper = section.strip().lower() == "helper"   # computed, but not written out
        self.text = text.strip()
        self.special = self.text if self.text.startswith("@") else None
        self.alternatives = [] if self.special else [self._parse(a) for a in self.text.split("|")]

    def _parse(self, alt):
        # Protect {Column Names} (they contain spaces) before splitting on whitespace.
        refs = []
        def keep(m):
            refs.append(m.group(1))
            return f"\x00{len(refs) - 1}\x00"
        alt = re.sub(r"\{([^}]*)\}", keep, alt)
        tokens = alt.split()
        terms, op = [], "+"
        for tok in tokens:
            if tok in "+-*/":
                op = tok
                continue
            m = self.TERM_RE.match(tok)
            name = m.group("name")
            if name.startswith("\x00"):
                kind, name = "ref", refs[int(name.strip("\x00"))]
            elif re.fullmatch(r"\d+(\.\d+)?", name):
                kind, name = "const", float(name)
            else:
                kind = "tag"
            sign = -1 if m.group("neg") else 1
            terms.append({"op": op, "sign": sign, "kind": kind, "name": name, "optional": bool(m.group("opt"))})
            op = "+"
        return terms

    def refs(self):
        return {t["name"] for alt in self.alternatives for t in alt if t["kind"] == "ref"}


def load_rules(path):
    with open(path, newline="", encoding="utf-8") as f:
        rules = [Rule(int(r["No"]), r["Column"], r["Rule"], r.get("Section", ""))
                 for r in csv.DictReader(f)]
    rules.sort(key=lambda r: r.no)
    return rules


def evaluation_order(rules):
    """Columns that reference other columns must be computed after them."""
    first_by_name = {}
    for r in rules:
        first_by_name.setdefault(r.column, r)
    order, state = [], {}

    def visit(r, path):
        if state.get(r.no) == "done":
            return
        if state.get(r.no) == "busy":
            raise ValueError("Circular reference in Fundamentals_Mapping.csv: " + " -> ".join(path + [r.column]))
        state[r.no] = "busy"
        for name in r.refs():
            if name not in first_by_name:
                raise ValueError(f"Column '{r.column}' refers to unknown column '{{{name}}}'")
            visit(first_by_name[name], path + [r.column])
        state[r.no] = "done"
        order.append(r)

    for r in rules:
        visit(r, [])
    return order, first_by_name


# --------------------------------------------------------------------------- SEC facts

def days(a, b):
    return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days


def is_annual_duration(e):
    return "start" in e and 300 <= days(e["start"], e["end"]) <= 400


class Company:
    """Annual figures for one company, organised by fiscal-year end date."""

    def __init__(self, data):
        self.cik = int(data.get("cik", 0))
        self.name = data.get("entityName", "")
        self.facts = data.get("facts", {})
        self.period_end_of_filing = self._filing_period_ends()
        self.fiscal_ends = sorted(set(self.period_end_of_filing.values()))

    def _annual_entries(self, namespaces=NAMESPACES):
        for ns in namespaces:
            for tag, body in self.facts.get(ns, {}).items():
                for unit, entries in body.get("units", {}).items():
                    for e in entries:
                        if e.get("form") in ANNUAL_FORMS:
                            yield ns, tag, unit, e

    def _filing_period_ends(self):
        """accession number -> fiscal year end it reports (latest ~12-month period in it)."""
        ends = {}
        for _ns, _tag, _unit, e in self._annual_entries(("us-gaap", "ifrs-full")):
            if is_annual_duration(e):
                if e["end"] > ends.get(e["accn"], ""):
                    ends[e["accn"]] = e["end"]
        return ends

    def year(self, fiscal_end, prefer="original"):
        return FiscalYear(self, fiscal_end, prefer)


class FiscalYear:
    def __init__(self, company, fiscal_end, prefer):
        self.company = company
        self.fiscal_end = fiscal_end
        own = {a for a, e in company.period_end_of_filing.items() if e == fiscal_end}
        best = {}  # (ns, tag) -> {unit: (rank, entry)}
        lengths = Counter()
        for ns, tag, unit, e in company._annual_entries():
            if e["end"] != fiscal_end:
                continue
            if "start" in e and not is_annual_duration(e):
                continue
            if "start" in e and e["accn"] in own:
                lengths[days(e["start"], e["end"]) + 1] += 1
            # Prefer figures from the 10-K that reports this year ("as first reported");
            # among those, and otherwise, take the latest filing (amendments / restatements).
            original = e["accn"] in own
            rank = (original if prefer == "original" else True, e.get("filed", ""))
            slot = best.setdefault((ns, tag), {})
            if unit not in slot or rank > slot[unit][0]:
                slot[unit] = (rank, e)
        self.values = {k: {u: re[1] for u, re in v.items()} for k, v in best.items()}
        self.period_length = lengths.most_common(1)[0][0] if lengths else None
        filed = [e.get("filed", "") for (ns, tag), units in self.values.items() for e in units.values()
                 if e["accn"] in own]
        self.report_date = min(filed) if filed else None
        self.currency = self._currency()

    def _currency(self):
        for key in (("us-gaap", "Assets"), ("ifrs-full", "Assets"), ("us-gaap", "StockholdersEquity")):
            for unit in self.values.get(key, {}):
                if re.fullmatch(r"[A-Z]{3}", unit):
                    return unit
        units = Counter(u for v in self.values.values() for u in v if re.fullmatch(r"[A-Z]{3}", u))
        return units.most_common(1)[0][0] if units else "USD"

    def tag(self, name):
        """Value of a tag converted to output units, or None."""
        if ":" in name:
            ns, tag = name.split(":", 1)
            candidates = [(ns, tag)]
        else:
            candidates = [(ns, name) for ns in NAMESPACES]
        cur = self.currency
        for key in candidates:
            units = self.values.get(key)
            if not units:
                continue
            if cur in units:
                return units[cur]["val"] / MONEY_SCALE
            if f"{cur}/shares" in units:
                return units[f"{cur}/shares"]["val"] / PER_SHARE_SCALE
            if "shares" in units:
                return units["shares"]["val"] / SHARE_SCALE
            if "pure" in units:
                return units["pure"]["val"]
        return None


# --------------------------------------------------------------------------- conversion

def fiscal_label(fiscal_end):
    d = dt.date.fromisoformat(fiscal_end)
    # The layout labels a year ending in early January (52/53-week years) with that year,
    # but years ending from mid-January to May with the previous year.
    if (d.month == 1 and d.day > 3) or 2 <= d.month <= 5:
        return f"{d.year - 1}/FY"
    return f"{d.year}/FY"


def evaluate(rule, fy, out, first_by_name):
    def term_value(t):
        if t["kind"] == "const":
            return t["name"]
        if t["kind"] == "ref":
            return out.get(first_by_name[t["name"]].no)
        return fy.tag(t["name"])

    for alt in rule.alternatives:
        values = [term_value(t) for t in alt]
        required = [v for t, v in zip(alt, values) if not t["optional"]]
        if any(v is None for v in required):
            continue
        if not required and all(v is None for v in values):
            continue
        total, product = 0.0, None
        ok = True
        for t, v in zip(alt, values):
            v = 0.0 if v is None else v * t["sign"]
            if t["op"] in "+-":
                if product is not None:
                    total += product
                product = v if t["op"] == "+" else -v
            elif t["op"] == "*":
                product *= v
            elif t["op"] == "/":
                if v == 0:
                    ok = False
                    break
                product /= v
        if not ok:
            continue
        return total + (product or 0.0)
    return None


def convert_year(company, fiscal_end, rules, order, first_by_name, ids, prefer, run_date):
    fy = company.year(fiscal_end, prefer)
    ident = ids.get(company.cik, {})
    out = {}
    specials = {
        "@ticker": ident.get("Ticker"),
        "@security_id": ident.get("Security ID"),
        "@sedol": ident.get("Sedol"),
        "@run_date": run_date,
        "@exchange": ident.get("Exchange"),
        "@fiscal_end": fiscal_end.replace("-", ""),
        "@interim": fiscal_label(fiscal_end),
        "@report_date": fy.report_date.replace("-", "") if fy.report_date else None,
        "@period_length": fy.period_length,
        "@currency": fy.currency,
        "@blank": "",
        "@none": None,
    }
    for rule in order:
        if rule.special:
            if rule.special not in specials:
                raise ValueError(f"Unknown special rule {rule.special} for column '{rule.column}'")
            out[rule.no] = specials[rule.special]
        else:
            out[rule.no] = evaluate(rule, fy, out, first_by_name)
    return [out[r.no] for r in rules if not r.helper]


def fmt(v):
    if v is None:
        return "-"
    if isinstance(v, str):
        return v
    if isinstance(v, float):
        if v == 0:
            return "0"
        if v.is_integer() and abs(v) < 1e15:
            return str(int(v))
        s = f"{v:.15g}"
        if "e" in s:
            s = f"{v:.15f}".rstrip("0").rstrip(".")
        return s
    return str(v)


# --------------------------------------------------------------------------- identifiers / download

def sec_get(url, user_agent):
    req = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def load_identifiers(tickers_file, crosswalk_file, user_agent):
    """CIK -> {Ticker, Exchange, Security ID, Sedol}."""
    ids = defaultdict(dict)
    if not os.path.exists(tickers_file) and user_agent:
        try:
            with open(tickers_file, "wb") as f:
                f.write(sec_get(SEC_TICKERS_URL, user_agent))
        except Exception as exc:  # noqa: BLE001 - optional enrichment
            print(f"Could not download ticker list ({exc}); tickers will be '-'", file=sys.stderr)
            if os.path.exists(tickers_file) and os.path.getsize(tickers_file) == 0:
                os.remove(tickers_file)
    if os.path.exists(tickers_file):
        data = json.load(open(tickers_file))
        fields = data["fields"]
        for row in data["data"]:
            r = dict(zip(fields, row))
            cik = int(r["cik"])
            if "Ticker" in ids[cik]:
                continue  # SEC lists the primary listing first
            ids[cik]["Ticker"] = r["ticker"].replace("-", ".") + "-US"
            if r.get("exchange"):
                ids[cik]["Exchange"] = EXCHANGE_NAMES.get(r["exchange"], r["exchange"])
    if crosswalk_file and os.path.exists(crosswalk_file):
        with open(crosswalk_file, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                cik = int(r.pop("CIK"))
                ids[cik].update({k: v for k, v in r.items() if v})
    return ids


def download(ciks, tickers, input_dir, user_agent, ids_file):
    if not user_agent:
        sys.exit("SEC requires a User-Agent with your name and e-mail: use --user-agent or set SEC_USER_AGENT")
    os.makedirs(input_dir, exist_ok=True)
    ciks = list(ciks)
    if tickers:
        if not os.path.exists(ids_file):
            with open(ids_file, "wb") as f:
                f.write(sec_get(SEC_TICKERS_URL, user_agent))
        data = json.load(open(ids_file))
        by_ticker = {row[2].upper(): int(row[0]) for row in data["data"]}
        for t in tickers:
            if t.upper() not in by_ticker:
                print(f"Ticker {t} not found in SEC list", file=sys.stderr)
            else:
                ciks.append(by_ticker[t.upper()])
    for cik in ciks:
        path = os.path.join(input_dir, f"CIK{cik:010d}.json")
        try:
            with open(path, "wb") as f:
                f.write(sec_get(SEC_FACTS_URL.format(cik=cik), user_agent))
            print(f"Downloaded {path}")
        except Exception as exc:  # noqa: BLE001
            print(f"Failed to download CIK {cik}: {exc}", file=sys.stderr)
            if os.path.exists(path):
                os.remove(path)
        time.sleep(0.12)  # SEC limit is 10 requests per second


def iter_company_json(input_dir, zip_path):
    if zip_path:
        with zipfile.ZipFile(zip_path) as z:
            for name in z.namelist():
                if name.endswith(".json"):
                    with z.open(name) as f:
                        yield name, json.load(io.TextIOWrapper(f, encoding="utf-8"))
    else:
        for name in sorted(os.listdir(input_dir)):
            if name.endswith(".json"):
                with open(os.path.join(input_dir, name), encoding="utf-8") as f:
                    yield name, json.load(f)


# --------------------------------------------------------------------------- main

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input", default=os.path.join(HERE, "testJSON"), help="folder of companyfacts JSON files")
    p.add_argument("--zip", help="SEC bulk companyfacts.zip (read directly, instead of --input)")
    p.add_argument("--output", default=os.path.join(HERE, "Output", "EDGAR_Fundamentals.txt"))
    p.add_argument("--mapping", default=os.path.join(HERE, "Fundamentals_Mapping.csv"))
    p.add_argument("--exclusion", default="",
                   help="optional file of tags that must never be used (e.g. Exclusion.txt); "
                        "off by default because Fundamentals_Mapping.csv already decides which tags are used")
    p.add_argument("--identifiers", default=os.path.join(HERE, "Identifiers.csv"),
                   help="optional CSV: CIK,Ticker,Security ID,Sedol,Exchange (overrides SEC data)")
    p.add_argument("--tickers-file", default=os.path.join(HERE, "company_tickers_exchange.json"))
    p.add_argument("--years", default="latest", help="'latest' (default), 'all', or a number N for the last N years")
    p.add_argument("--prefer", choices=["original", "restated"], default="original",
                   help="use figures as first reported (default) or the latest restated figures")
    p.add_argument("--delimiter", default="|", help="'|' (default) or ',' for CSV")
    p.add_argument("--user-agent", default=os.environ.get("SEC_USER_AGENT"),
                   help="required for downloads, e.g. 'Jane Doe jane@example.com'")
    p.add_argument("--cik", default="", help="comma-separated CIKs to download into --input first")
    p.add_argument("--tickers", default="", help="comma-separated tickers to download into --input first")
    a = p.parse_args()

    if a.cik or a.tickers:
        download([int(c) for c in a.cik.split(",") if c], [t for t in a.tickers.split(",") if t],
                 a.input, a.user_agent, a.tickers_file)

    rules = load_rules(a.mapping)
    order, first_by_name = evaluation_order(rules)
    excluded = set()
    if a.exclusion and os.path.exists(a.exclusion):
        excluded = {l.strip() for l in open(a.exclusion) if l.strip()}
    ids = load_identifiers(a.tickers_file, a.identifiers, a.user_agent)
    run_date = dt.date.today().strftime("%Y%m%d")

    os.makedirs(os.path.dirname(os.path.abspath(a.output)), exist_ok=True)
    n_rows = n_skipped = 0
    with open(a.output, "w", encoding="utf-8", newline="") as out:
        if a.delimiter == "|":
            out.write("| \n")  # the layout starts with this line
            write = lambda cells: out.write("|".join(cells) + "\n")
        else:
            writer = csv.writer(out, delimiter=a.delimiter, lineterminator="\n")
            write = writer.writerow
        write([r.column for r in rules if not r.helper])
        for name, data in iter_company_json(a.input, a.zip):
            if "facts" not in data or not data["facts"]:
                n_skipped += 1
                continue
            for ns in data["facts"].values():
                for tag in excluded & ns.keys():
                    del ns[tag]
            company = Company(data)
            if not company.fiscal_ends:
                print(f"{name}: no annual (10-K / 20-F) figures, skipped", file=sys.stderr)
                n_skipped += 1
                continue
            ends = company.fiscal_ends
            if a.years == "latest":
                ends = ends[-1:]
            elif a.years != "all":
                ends = ends[-int(a.years):]
            for fe in reversed(ends):
                row = convert_year(company, fe, rules, order, first_by_name, ids, a.prefer, run_date)
                write([fmt(v) for v in row])
                n_rows += 1
    print(f"Wrote {n_rows} rows to {a.output} ({n_skipped} files skipped)")


if __name__ == "__main__":
    main()
