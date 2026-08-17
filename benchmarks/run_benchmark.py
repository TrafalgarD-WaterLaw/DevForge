"""DevForge 评测基准（B1）—— 标准任务集 headless 跑全流程，产出指标。

用法（项目根目录）:
    python benchmarks/run_benchmark.py                 # 跑全部任务
    python benchmarks/run_benchmark.py wordcount       # 跑单个任务
    python benchmarks/run_benchmark.py --from ledger   # 从 ledger 起续跑
    python benchmarks/run_benchmark.py --sandbox off   # 宿主机直跑（对比环境）

指标（写 benchmarks/results.json + 控制台表格）:
    delivered   交付物存在（WareHouse 目录 + .py 文件）
    verdict     质检结论（PASS/WARN/FAIL）
    score       质检得分
    duration_s  总耗时（含阶段明细）
    tokens      LLM 总消耗（prompt + completion）
    calls       LLM 调用次数
    failed      质检耗尽失败标记

headless 运行（无 ws）：PM 跳过提问直接生成需求摘要，适合自动化评测。
评测真实消耗 API token —— 改动 prompt/流程后用同一任务集对比前后指标。
"""
import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))  # src-layout
# GBK 控制台打印 emoji 会崩（实测 UnicodeEncodeError）—— 强制 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 完整日志落盘（tee）：管道 tail 会丢输出，进程静默死亡时靠日志排障
_LOG_PATH = Path(__file__).resolve().parent / "last_run.log"
try:
    _log_fh = open(_LOG_PATH, "a", encoding="utf-8")
    _log_fh.write(f"\n\n{'=' * 60}\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
                  f"benchmark start (pid={os.getpid()})\n{'=' * 60}\n")
    _log_fh.flush()
    _stdout = sys.stdout

    class _Tee:
        def write(self, s):
            _log_fh.write(s)
            _stdout.write(s)
        def flush(self):
            _log_fh.flush()
            _stdout.flush()

    sys.stdout = _Tee()
except OSError:
    _log_fh = None

from core.config import load_pipeline_config
from codegen.application.chat_chain import ChatChain, artifact_path
from core.events import HookRegistry

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

RESULTS_FILE = Path(__file__).resolve().parent / "results.json"
LOGS_DIR = Path(__file__).resolve().parent / "logs"


def _events_of(project_dir: str) -> list[dict]:
    path = Path(artifact_path(project_dir, "run_events.json"))
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("events", data) if isinstance(data, dict) else []
    except (json.JSONDecodeError, OSError):
        return []


def _metrics_from_blackboard(chain: ChatChain, phase_times: dict) -> dict:
    """指标从 blackboard 直接提取 —— 不依赖 run_events.json
    （benchmark 直调 chain.run()，事件未由 runner 落盘）。"""
    bb = chain.blackboard
    qg = bb.get("quality_gate", {}) or {}
    usage = bb.get("usage_log", {}) or {}
    prompt = sum(e.get("prompt_tokens", 0) for e in usage.values())
    completion = sum(e.get("completion_tokens", 0) for e in usage.values())
    calls = sum(e.get("calls", 0) for e in usage.values())
    return {
        "verdict": qg.get("verdict", "?"),
        "score": qg.get("score", None),
        "tokens": prompt + completion,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "calls": calls,
        "failed": bool(bb.get("quality_gate_failed")),
        "qg_loops": bb.get("quality_gate_loops", 0),
        "phases_s": phase_times,
    }


def _collect_phase_times() -> dict:
    """注册 phase_end hook 收集各阶段耗时（benchmark 直调不走 runner）。"""
    times: dict[str, float] = {}

    def _on_phase_end(event: str, **data):
        if event == "phase_end" and data.get("phase"):
            times[data["phase"]] = round(data.get("elapsed", 0), 1)

    HookRegistry.on("phase_end", _on_phase_end)
    return times, _on_phase_end


def run_single_task(task: dict, run_id: str, *, sandbox: str) -> dict:
    print(f"\n{'=' * 64}\n  [{task['name']}] {task['prompt'][:60]}...\n{'=' * 64}")
    result = {"name": task["name"], "delivered": False, "error": None,
              "duration_s": 0.0, "verdict": "?", "score": None,
              "tokens": 0, "prompt_tokens": 0, "completion_tokens": 0,
              "calls": 0, "failed": False, "qg_loops": 0,
              "phases_s": {}, "project_dir": ""}
    try:
        # M1: 隔离记忆库 —— 评测任务不污染生产记忆（生产库用全局 .memory/）
        bench_mem = str(Path(__file__).resolve().parent / ".memory" / "chroma")
        cfg = load_pipeline_config("default")
        if sandbox:
            cfg.setdefault("tools", {})["sandbox"] = sandbox
        chain = ChatChain(config=cfg, task_prompt=task["prompt"],
                          run_id=run_id, memory_dir=bench_mem)
        times, _hook = _collect_phase_times()
        start = time.time()
        project_dir = chain.run()
        result["duration_s"] = round(time.time() - start, 1)
        result["phases_s"] = times
        HookRegistry.off("phase_end", _hook)
        result["project_dir"] = project_dir
        result["delivered"] = bool(project_dir and os.path.isdir(project_dir)
                                   and any(Path(project_dir).rglob("*.py")))
        result.update(_metrics_from_blackboard(chain, times))
        # 事件落盘（供历史页/排障浏览——runner 正常路径会做，benchmark 直调需补）
        try:
            from serving.application.ws_manager import complete_run, persist_run
            complete_run(run_id, project_dir)
            persist_run(run_id, task["prompt"])
        except Exception:
            pass
    except Exception as e:
        result["error"] = str(e)[:200]
        print(f"  [FAIL] {e}")
    print(f"  → {result['verdict']} score={result['score']} "
          f"{result['duration_s']}s {result['tokens']} tok "
          f"calls={result['calls']} loops={result['qg_loops']} "
          f"failed={result['failed']}")
    # 单任务日志落盘（排障用）
    try:
        LOGS_DIR.mkdir(exist_ok=True)
        (LOGS_DIR / f"{task['name']}.log").write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8")
    except OSError:
        pass
    return result


def main():
    ap = argparse.ArgumentParser(description="DevForge benchmark runner")
    ap.add_argument("task", nargs="?", default="",
                    help="single task name (default: all tasks in tasks.json)")
    ap.add_argument("--from", dest="start_from", default="",
                    help="resume from this task name (inclusive)")
    ap.add_argument("--sandbox", default=None, choices=["off", "docker"],
                    help="override tools.sandbox (off = host direct run)")
    args = ap.parse_args()

    tasks = json.loads(
        (Path(__file__).parent / "tasks.json").read_text(encoding="utf-8"))["tasks"]
    if args.task:
        tasks = [t for t in tasks if t["name"] == args.task] or tasks[:1]
    if args.start_from:
        idx = next((i for i, t in enumerate(tasks)
                    if t["name"] == args.start_from), 0)
        tasks = tasks[idx:]
        print(f"（从 {args.start_from} 起续跑，共 {len(tasks)} 个任务）")

    ts = time.strftime("%Y%m%d_%H%M%S")
    results = []
    for i, task in enumerate(tasks):
        results.append(run_single_task(task, f"bench-{task['name']}-{ts}-{i}",
                                       sandbox=args.sandbox))

    prev = json.loads(RESULTS_FILE.read_text(encoding="utf-8")) \
        if RESULTS_FILE.exists() else []
    RESULTS_FILE.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                            encoding="utf-8")

    # 汇总表
    print("\n" + "=" * 92)
    print(f"{'任务':<16}{'交付':<6}{'结论':<8}{'得分':<6}"
          f"{'耗时':<9}{'tokens':<11}{'调用':<6}{'失败'}")
    print("-" * 92)
    delivered = verdict_pass = 0
    total_tokens = total_time = 0
    for r in results:
        delivered += r["delivered"]
        verdict_pass += r["verdict"] == "PASS"
        total_tokens += r["tokens"]
        total_time += r["duration_s"]
        print(f"{r['name']:<16}{'✅' if r['delivered'] else '❌':<6}"
              f"{r['verdict']:<8}{str(r['score']):<6}"
              f"{r['duration_s']:.0f}s{'':<5}{r['tokens']:<11}{r['calls']:<6}"
              f"{'⚠️' if r['failed'] else ''}")
        if r.get("phases_s"):
            detail = " ".join(f"{k}:{v}s" for k, v in r["phases_s"].items())
            print(f"    {'':<16}阶段: {detail}")
    print("-" * 92)
    print(f"交付率 {delivered}/{len(results)} · PASS 率 "
          f"{verdict_pass}/{len(results)} · 总耗时 {total_time:.0f}s · "
          f"总 token {total_tokens:,}（prompt "
          f"{sum(r['prompt_tokens'] for r in results):,} + completion "
          f"{sum(r['completion_tokens'] for r in results):,}）")
    if prev:
        prev_by = {r["name"]: r for r in prev}
        print("\n对比上次（同一任务集）:")
        for r in results:
            p = prev_by.get(r["name"])
            if not p:
                continue
            dt = (r["tokens"] or 0) - (p.get("tokens") or 0)
            ds = (r["duration_s"] or 0) - (p.get("duration_s") or 0)
            print(f"  {r['name']:<16} {p.get('verdict')}→{r['verdict']} "
                  f"tokens {p.get('tokens')}→{r['tokens']} "
                  f"({'↓' if dt < 0 else '↑'}{abs(dt)}) "
                  f"耗时 {'↓' if ds < 0 else '↑'}{abs(ds):.0f}s")
    print("=" * 92)
    print(f"结果已写入 {RESULTS_FILE}")


if __name__ == "__main__":
    main()
