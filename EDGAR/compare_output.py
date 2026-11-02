"""
compare_output.py - check EDGAR_to_Fundamentals.py output against a reference file in the same layout.

    python compare_output.py reference.txt Output/EDGAR_Fundamentals.txt

Rows are matched on Ticker + Fiscal End. For every column it reports how many
companies match (within 1% or 1 thousand), plus the mismatches so you can tune
Fundamentals_Mapping.csv. Columns are compared by position, because the header
contains "Interest Income" twice.
"""
import argparse
from collections import defaultdict


def read(path):
    lines = [l.rstrip("\n") for l in open(path, encoding="utf-8")]
    if lines and lines[0].strip() == "|":
        lines = lines[1:]
    header = lines[0].split("|")
    rows = {}
    for l in lines[1:]:
        p = l.split("|")
        rows[(p[0], p[5])] = p
    return header, rows


def num(s):
    try:
        return float(s)
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("reference")
    ap.add_argument("ours")
    ap.add_argument("--details", action="store_true", help="list every mismatch")
    a = ap.parse_args()
    header, ref = read(a.reference)
    _, ours = read(a.ours)
    keys = sorted(k for k in ours if k in ref and k[0] != "-")
    print(f"{len(keys)} company-years found in both files\n")
    if not keys:
        return
    stats = defaultdict(lambda: [0, 0, 0])  # match, mismatch, missing in ours
    bad = defaultdict(list)
    for k in keys:
        f, o = ref[k], ours[k]
        for i in range(7, 84):
            fv, ov = num(f[i]), num(o[i])
            if fv is None:
                continue
            if ov is None:
                if fv == 0:
                    stats[i][0] += 1
                else:
                    stats[i][2] += 1
                    bad[i].append((k[0], f[i], "-"))
                continue
            if abs(fv - ov) <= max(1.0, abs(fv) * 0.01):
                stats[i][0] += 1
            else:
                stats[i][1] += 1
                bad[i].append((k[0], f[i], o[i]))
        for i in (89, 90):
            fv, ov = num(f[i]), num(o[i])
            if fv is not None:
                ok = ov is not None and abs(fv - ov) <= max(1.0, abs(fv) * 0.01)
                stats[i][0 if ok else (1 if ov is not None else 2)] += 1
                if not ok:
                    bad[i].append((k[0], f[i], o[i]))
    tot = [0, 0, 0]
    print(f"{'#':>3} {'Column':50s} {'match':>5} {'wrong':>5} {'blank':>5}")
    for i in sorted(stats):
        m, w, b = stats[i]
        tot = [tot[0] + m, tot[1] + w, tot[2] + b]
        print(f"{i + 1:3d} {header[i][:50]:50s} {m:5d} {w:5d} {b:5d}")
        if a.details:
            for t, fv, ov in bad[i]:
                print(f"      {t:12s} reference={fv:>18s}  ours={ov}")
    n = sum(tot)
    print(f"\nOverall: {tot[0]}/{n} values match ({100 * tot[0] / n:.1f}%), "
          f"{tot[1]} differ, {tot[2]} missing")


if __name__ == "__main__":
    main()
