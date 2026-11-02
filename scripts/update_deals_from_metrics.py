#!/usr/bin/env python3
"""Rebuild leaderboard.json `deals.ytd` (TV "Top 3 YTD Sales Volume / Closings")
from Metrics Tracker working-agent closings.

Rule (approved by Kevin 2026-11-02 1:52 PM CT): per-agent closed SALES are credited
to the working agent from Sisu/SkySlope; team totals stay on eXp transaction IDs.
Units can be fractional (one agent on both sides counts 2, split deals 0.5) and
are written exactly as given.

Source: newest /workspace/metrics/data/<YYYY-MM-DD>/metrics.json (or --metrics PATH)
  units  = segments["agent:<fubId>"].wa_closed.ytd
  volume = segments["agent:<fubId>"].wa_closed_vol.ytd
  name   = agents["agent:<fubId>"]
  as-of  = metrics "generated" ("YYYY-MM-DD hh:mm AM/PM CT"), stamped as dealsAsOf in Central

Only `dealsAsOf` and `deals` change. MTD (agents/mtdAll/totals/asOf) and the YTD
activity block (ytd/ytdAsOf/ytdTotals) stay on FUB and are left untouched.

Usage (repo root):
  python3 scripts/update_deals_from_metrics.py              # write leaderboard.json
  python3 scripts/update_deals_from_metrics.py --dry-run    # print, write nothing
  python3 scripts/update_deals_from_metrics.py --metrics /path/to/metrics.json

Exit 2 and leave leaderboard.json unchanged on any failure (missing/unreadable
metrics, missing wa_* fields, unparseable as-of, metrics older than the current
dealsAsOf, fewer than 3 agents with closings). No extra packages.
"""
import argparse, datetime, glob, json, os, re, sys, tempfile
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LB = os.path.join(ROOT, "leaderboard.json")
DATA = "/workspace/metrics/data"
CT = ZoneInfo("America/Chicago")
TOP_N = 15
SOURCE = "Metrics Tracker, working-agent closings (Sisu/SkySlope)"
EXCLUDE = ["Transaction Coordinator", "Transaction Coordinator 2", "Training Agent", "Jesse Jones"]
# Metrics Tracker buckets that are not people.
NON_PERSON_KEYS = {"agent:n-fubother", "agent:n-unassigned"}


def stop(msg):
    print("update_deals_from_metrics: " + msg + " (leaderboard.json unchanged)", file=sys.stderr)
    sys.exit(2)


def newest_metrics():
    c = sorted(p for p in glob.glob(os.path.join(DATA, "*", "metrics.json"))
               if re.fullmatch(r"\d{4}-\d{2}-\d{2}", os.path.basename(os.path.dirname(p))))
    if not c:
        stop("no metrics.json under " + DATA)
    return c[-1]


def parse_asof(s):
    m = re.fullmatch(r"\s*(\d{4}-\d{2}-\d{2}) (\d{1,2}:\d{2} [AP]M) CT\s*", str(s or ""))
    if not m:
        stop("cannot parse metrics 'generated' as-of: %r" % (s,))
    dt = datetime.datetime.strptime(m.group(1) + " " + m.group(2), "%Y-%m-%d %I:%M %p")
    return dt.replace(tzinfo=CT)


def money(v):
    if v >= 1e6:
        return "$%.2fM" % (v / 1e6)
    t = "%.1f" % (v / 1e3)
    return "$" + (t[:-2] if t.endswith(".0") else t) + "K"


def num(v):
    return int(v) if float(v).is_integer() else v


def clean_name(n):
    n = str(n).replace("\u2019", "'").strip()
    return re.sub(r"\s*\(not in FUB\)$", "", n)


def build(metrics_path):
    try:
        with open(metrics_path) as f:
            m = json.load(f)
    except Exception as e:
        stop("cannot read %s: %s" % (metrics_path, e))
    asof = parse_asof(m.get("generated"))
    agents, segs = m.get("agents"), m.get("segments")
    if not isinstance(agents, dict) or not isinstance(segs, dict):
        stop("metrics.json has no agents/segments maps")
    excl = {x.lower() for x in EXCLUDE}
    rows, missing = [], []
    for key, raw in agents.items():
        if not key.startswith("agent:") or key in NON_PERSON_KEYS:
            continue
        name = clean_name(raw)
        if name.lower() in excl:
            continue
        s = segs.get(key) or {}
        try:
            units = float(s["wa_closed"]["ytd"])
            vol = float(s["wa_closed_vol"]["ytd"])
        except (KeyError, TypeError, ValueError):
            missing.append(key)
            continue
        if units <= 0 and vol <= 0:
            continue
        r = {"name": name, "volume": num(round(vol, 2)), "volumeText": money(vol)}
        if units > 0:
            avg = vol / units
            r["dealAvg"] = round(avg)
            r["dealAvgText"] = money(avg)
        r["closed"] = num(units)
        r["_key"] = key
        rows.append(r)
    if missing and len(missing) > len(agents) // 2:
        stop("wa_closed/wa_closed_vol missing for %d of %d agents" % (len(missing), len(agents)))
    if sum(1 for r in rows if r["closed"] > 0) < 3:
        stop("fewer than 3 agents with working-agent closings")
    by_vol = sorted(rows, key=lambda r: (-r["volume"], -r["closed"]))
    top = by_vol[:TOP_N]
    # Make sure the closings top 3 can never fall outside the list.
    for r in sorted(rows, key=lambda r: (-r["closed"], -r["volume"]))[:3]:
        if r not in top:
            top.append(r)
    for r in top:
        r.pop("_key")
    return asof, top


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--metrics", help="metrics.json path (default: newest under %s)" % DATA)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="allow metrics older than current dealsAsOf")
    a = ap.parse_args()
    path = a.metrics or newest_metrics()
    try:
        with open(LB) as f:
            lb = json.load(f)
    except Exception as e:
        stop("cannot read leaderboard.json: %s" % e)
    asof, ytd = build(path)
    old = lb.get("dealsAsOf")
    if old and not a.force:
        try:
            if datetime.datetime.fromisoformat(old) > asof:
                stop("metrics as-of %s is older than current dealsAsOf %s" % (asof.isoformat(), old))
        except ValueError:
            pass
    deals = dict(lb.get("deals") or {})
    deals["source"] = SOURCE
    deals["exclude"] = list(dict.fromkeys(list(deals.get("exclude") or []) + EXCLUDE))
    deals["ytd"] = ytd
    lb["dealsAsOf"] = asof.isoformat()
    lb["deals"] = deals
    out = json.dumps(lb, indent=2)
    print("metrics: %s  as-of %s  rows %d" % (path, lb["dealsAsOf"], len(ytd)))
    for r in ytd[:3]:
        print("  vol  %-28s %s  (%s closings)" % (r["name"], r["volumeText"], r["closed"]))
    for r in sorted(ytd, key=lambda r: (-r["closed"], -r["volume"]))[:3]:
        print("  unit %-28s %s  (%s)" % (r["name"], r["closed"], r["volumeText"]))
    if a.dry_run:
        return
    fd, tmp = tempfile.mkstemp(dir=ROOT, prefix=".leaderboard.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(out)
        json.load(open(tmp))
        os.replace(tmp, LB)
    except Exception as e:
        if os.path.exists(tmp):
            os.unlink(tmp)
        stop("write failed: %s" % e)


if __name__ == "__main__":
    main()
