"""Run the pysdrlib overhead benchmarks and compare against a baseline.

    python tests/bench/run.py                   run everything, print a table
    python tests/bench/run.py --quick           faster, noisier
    python tests/bench/run.py --hardware        also stream from a real HackRF
    python tests/bench/run.py --update-baseline record this machine's baseline (.benchmarks/baseline.json)
    python tests/bench/run.py --check           compare to the baseline; exit 1 on a regression

Baselines are per machine (timings don't transfer between computers), so .benchmarks/ is gitignored.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import suite  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DIR = ROOT / ".benchmarks"
BASELINE = DIR / "baseline.json"


def _load(path):
    with open(path) as f:
        return json.load(f)


def _save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _show(rows, base, cur, threshold):
    mark = {"regression": "SLOWER", "improvement": "faster", "ok": ""}
    for name, status, bv, cv, change in rows:
        if status == "ok" and abs(change) < 0.05:
            continue
        e = cur["results"][name]
        if e["kind"] == "time":
            print(f"  {mark[status]:<7} {name:<34}{suite._fmt_time(bv)} -> {suite._fmt_time(cv)}  ({change:+.0%})")
        else:
            print(f"  {mark[status]:<7} {name:<34}{bv:.4g} -> {cv:.4g} {e['unit']}  ({change:+.0%})")


def _merge_best(old, new):
    """Keep the better of two measurements of the same benchmark"""
    for name, n in new["results"].items():
        o = old["results"].get(name)
        if o is None:
            old["results"][name] = n
        elif n["kind"] == "time":
            if n["best"] < o["best"]:
                old["results"][name] = n
        elif (n["value"] < o["value"]) == n["lower_is_better"]:
            old["results"][name] = n


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true", help="fewer repeats, skip the slow benchmarks")
    ap.add_argument("--hardware", action="store_true", help="include benchmarks that need a HackRF")
    ap.add_argument("--group", action="append", help="only this group (repeatable)")
    ap.add_argument("--name", action="append", help="only this benchmark (repeatable)")
    ap.add_argument("--list", action="store_true", help="list benchmarks and exit")
    ap.add_argument("--json", metavar="PATH", help="write results to PATH")
    ap.add_argument("--save", action="store_true", help="also keep the run in .benchmarks/runs/")
    ap.add_argument("--baseline", metavar="PATH", default=str(BASELINE), help="baseline file (default: %(default)s)")
    ap.add_argument("--update-baseline", action="store_true", help="write this run as the baseline")
    ap.add_argument("--compare", metavar="PATH", help="compare to a results file and report")
    ap.add_argument("--check", action="store_true", help="compare to the baseline; exit 1 on regression")
    ap.add_argument("--threshold", type=float, default=0.25, help="relative slowdown that counts as a regression")
    args = ap.parse_args(argv)

    if args.list:
        for spec in suite.REGISTRY.values():
            print(f"{spec.group:<9}{spec.name}{'  (needs hardware)' if spec.hardware else ''}{'  (slow)' if spec.slow else ''}")
        return 0

    names = set(args.name) if args.name else None
    groups = set(args.group) if args.group else None
    run = lambda only=None: suite.run(names=only or names, groups=groups, quick=args.quick,
                                      hardware=args.hardware, log=print)

    print("pysdrlib benchmarks" + (" (quick)" if args.quick else ""))
    cur = run()
    m = cur["meta"]
    print(f"\n{m['commit']}{'+dirty' if m['dirty'] else ''}  {m['cpu']}  python {m['python']}  numpy {m['numpy']}")

    if args.json:
        _save(args.json, cur)
    if args.save:
        tag = f"{m['commit']}{'-dirty' if m['dirty'] else ''}-{m['time'].replace(':', '')}"
        _save(DIR / "runs" / f"{tag}.json", cur)
    if args.update_baseline:
        _save(args.baseline, cur)
        print(f"baseline written: {args.baseline}")
        return 0

    base_path = args.compare or (args.baseline if args.check else None)
    if base_path is None:
        return 0
    if not Path(base_path).exists():
        if args.check:
            _save(base_path, cur)
            print(f"no baseline yet; recorded this run as {base_path}")
            return 0
        print(f"no such file: {base_path}")
        return 2

    base = _load(base_path)
    bm = base["meta"]
    if bm.get("cpu") != m["cpu"] or bm.get("machine") != m["machine"]:
        print(f"WARNING: baseline is from a different machine ({bm.get('cpu')}); comparison is unreliable")
    if bm.get("quick") != m["quick"]:
        print("WARNING: baseline and this run use different --quick settings; expect noise")

    rows = suite.compare(base, cur, args.threshold)
    suspects = {r[0] for r in rows if r[1] == "regression"}
    if suspects:
        print(f"\nre-measuring {len(suspects)} suspected regression(s) to rule out noise...")
        again = suite.run(names=suspects, quick=args.quick, hardware=args.hardware)
        _merge_best(cur, again)
        rows = suite.compare(base, cur, args.threshold)

    print(f"\ncompared with {bm.get('commit')}{'+dirty' if bm.get('dirty') else ''} "
          f"(threshold {args.threshold:.0%}):")
    _show(rows, base, cur, args.threshold)
    bad = [r for r in rows if r[1] == "regression"]
    better = [r for r in rows if r[1] == "improvement"]
    print(f"\n{len(rows)} compared, {len(bad)} regression(s), {len(better)} improvement(s)")
    return 1 if bad and args.check else 0


if __name__ == "__main__":
    sys.exit(main())
