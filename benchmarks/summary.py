"""汇总所有基准任务结果 — 从 WareHouse checkpoint 重建（results.json 会被
单任务运行覆盖，checkpoint 是权威数据）。每个任务取最新一次运行。

用法: python benchmarks/summary.py
输出: 控制台表格 + benchmarks/results_all.json（完整基线快照）
"""
import glob
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))  # src-layout
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent


def collect() -> list[dict]:
    rows: list[dict] = []
    for d in glob.glob(str(ROOT / "WareHouse" / "*_bench-*")):
        dd = os.path.join(d, ".devforge")
        qg_path = os.path.join(dd, "checkpoint_QualityGate.json")
        if not os.path.exists(qg_path):
            continue
        try:
            qg = json.load(open(qg_path, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        g = qg.get("quality_gate", {})
        usage = qg.get("usage_log", {})
        tokens = sum(e.get("prompt_tokens", 0) for e in usage.values())
        calls = sum(e.get("calls", 0) for e in usage.values())
        dur = (os.path.getmtime(os.path.join(dd, "checkpoint.json"))
               - os.path.getmtime(os.path.join(dd, "task.txt")))
        rows.append({
            "name": d.split("bench-")[1].split("-")[0],
            "run_at": os.path.getmtime(os.path.join(dd, "checkpoint.json")),
            "delivered": bool(list(glob.glob(d + "/*.py"))),
            "verdict": g.get("verdict", "?"),
            "score": g.get("score"),
            "dur_min": round(dur / 60, 1),
            "tokens": tokens,
            "calls": calls,
            "loops": qg.get("quality_gate_loops", 0),
            "failed": bool(qg.get("quality_gate_failed")),
            "evidence": [f.get("name", "")[:20] for f in g.get("features", [])
                         if f.get("source") == "evidence"],
            "missing": [f.get("name", "")[:24] for f in g.get("features", [])
                        if f.get("status") in ("NO", "PARTIAL")
                        and f.get("source") != "evidence"],
        })
    # 每个任务取最新一次运行
    latest: dict[str, dict] = {}
    for r in rows:
        if r["name"] not in latest or r["run_at"] > latest[r["name"]]["run_at"]:
            latest[r["name"]] = r
    return list(latest.values())


def main():
    rows = collect()
    if not rows:
        print("（暂无完成的基准任务）")
        return
    rows.sort(key=lambda r: r["name"])
    print(f"{'任务':<16}{'交付':<4}{'结论':<6}{'得分':<5}"
          f"{'耗时':<7}{'tokens':<9}{'回跳':<4}")
    print("-" * 56)
    for r in rows:
        print(f"{r['name']:<16}{'Y' if r['delivered'] else 'N':<4}"
              f"{r['verdict']:<6}{str(r['score']):<5}"
              f"{r['dur_min']}m{'':<4}{r['tokens']:<9}{r['loops']:<4}")
        if r["evidence"]:
            print(f"    ⚠️ 证据: {r['evidence']}")
        if r["missing"]:
            print(f"    ❌ 缺功能: {r['missing']}")
    n = len(rows)
    deliv = sum(r["delivered"] for r in rows)
    passed = sum(r["verdict"] == "PASS" for r in rows)
    print("-" * 56)
    print(f"交付率 {deliv}/{n} · PASS 率 {passed}/{n} · "
          f"平均 {sum(r['dur_min'] for r in rows) / n:.1f}min/任务 · "
          f"总 tokens {sum(r['tokens'] for r in rows):,}")
    out = Path(__file__).resolve().parent / "results_all.json"
    rows_out = sorted(rows, key=lambda r: r["name"])
    for r in rows_out:
        r.pop("run_at", None)
    out.write_text(json.dumps(rows_out, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\n已写入 {out}")


if __name__ == "__main__":
    main()
