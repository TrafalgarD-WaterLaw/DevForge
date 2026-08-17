"""汇总所有基准任务结果。

数据源（按优先级）:
    1. benchmarks/logs/*.log —— run_benchmark.py 每次运行的单任务日志
       （含准确 duration_s / tokens / phases_s，权威数据）
    2. WareHouse/*_bench-* checkpoint —— 兜底重建（无 logs 时）

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
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = Path(__file__).resolve().parent / "logs"


def _from_logs() -> list[dict]:
    """从单任务日志读取（run_benchmark 写的权威数据，含准确耗时/阶段明细）。"""
    rows = []
    for lp in glob.glob(str(LOGS_DIR / "*.log")):
        try:
            r = json.loads(Path(lp).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not r.get("name"):
            continue
        rows.append({
            "name": r["name"],
            "run_at": os.path.getmtime(lp),
            "delivered": bool(r.get("delivered")),
            "verdict": r.get("verdict", "?"),
            "score": r.get("score"),
            "duration_s": r.get("duration_s", 0),
            "tokens": r.get("tokens", 0),
            "calls": r.get("calls", 0),
            "loops": r.get("qg_loops", 0),
            "failed": bool(r.get("failed")),
            "phases_s": r.get("phases_s", {}),
            "missing": [],
            "evidence": [],
        })
    return rows


def _from_checkpoints() -> list[dict]:
    """从 WareHouse checkpoint 重建（logs 缺失时的兜底）。"""
    rows = []
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
        # 任务名从目录名解析：前缀_DevForge_时间戳_bench-{name}-{ts}-{i}
        name = d.split("_bench-", 1)[-1].split("-")[0] if "_bench-" in d \
            else os.path.basename(d)[:20]
        rows.append({
            "name": name,
            "run_at": os.path.getmtime(os.path.join(dd, "checkpoint.json")),
            "delivered": bool(list(glob.glob(d + "/*.py"))),
            "verdict": g.get("verdict", "?"),
            "score": g.get("score"),
            "duration_s": dur,
            "tokens": tokens,
            "calls": calls,
            "loops": qg.get("quality_gate_loops", 0),
            "failed": bool(qg.get("quality_gate_failed")),
            "phases_s": {},
            "evidence": [f.get("name", "")[:20] for f in g.get("features", [])
                         if f.get("source") == "evidence"],
            "missing": [f.get("name", "")[:24] for f in g.get("features", [])
                        if f.get("status") in ("NO", "PARTIAL")
                        and f.get("source") != "evidence"],
        })
    return rows


def collect() -> list[dict]:
    rows = _from_logs() or _from_checkpoints()
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
          f"{'耗时':<7}{'tokens':<10}{'回跳':<4}")
    print("-" * 62)
    for r in rows:
        dur = (f"{r['duration_s']:.0f}s" if r.get("duration_s")
               else f"{r.get('dur_min', 0)}m")
        print(f"{r['name']:<16}{'Y' if r['delivered'] else 'N':<4}"
              f"{r['verdict']:<6}{str(r['score']):<5}"
              f"{dur:<7}{r['tokens']:<10}{r['loops']:<4}")
        if r.get("evidence"):
            print(f"    ⚠️ 证据: {r['evidence']}")
        if r.get("missing"):
            print(f"    ❌ 缺功能: {r['missing']}")
    n = len(rows)
    deliv = sum(r["delivered"] for r in rows)
    passed = sum(r["verdict"] == "PASS" for r in rows)
    print("-" * 62)
    print(f"交付率 {deliv}/{n} · PASS 率 {passed}/{n} · "
          f"平均 {sum(r.get('duration_s') or 0 for r in rows) / n:.0f}s/任务 · "
          f"总 tokens {sum(r['tokens'] for r in rows):,}")
    out = Path(__file__).resolve().parent / "results_all.json"
    rows_out = sorted(rows, key=lambda r: r["name"])
    for r in rows_out:
        r.pop("run_at", None)
    out.write_text(json.dumps(rows_out, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\n已写入 {out}（数据源: "
          f"{'logs/' if _from_logs() else 'checkpoint 重建'}）")


if __name__ == "__main__":
    main()
