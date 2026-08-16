"""DevForge benchmark runner — evaluates ChatChain performance on sample tasks."""
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import load_pipeline_config
from codegen.application.chat_chain import ChatChain

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

BENCHMARK_TASKS = [
    {
        "name": "Calculator",
        "prompt": "Design a simple calculator app in Python that supports add, subtract, "
                  "multiply, and divide operations. It should have a command-line interface "
                  "that accepts two numbers and an operator, then prints the result.",
    },
    {
        "name": "TodoApp",
        "prompt": "Build a command-line todo list application in Python. It should support "
                  "adding tasks, listing all tasks, marking tasks as complete, and deleting "
                  "tasks. Tasks should be persisted to a JSON file so they survive restarts.",
    },
    {
        "name": "PasswordGen",
        "prompt": "Create a command-line password generator in Python. It should let the "
                  "user specify length (default 16), whether to include uppercase, digits, "
                  "and symbols. Generate a random password matching the criteria and print it.",
    },
]


def run_single_task(task: dict) -> dict:
    """Execute one benchmark task and return its result."""
    print(f"\n{'='*60}")
    print(f"  Task: {task['name']}")
    print(f"  Prompt: {task['prompt'][:80]}...")
    print(f"{'='*60}")

    result = {"name": task["name"], "success": False, "error": None,
              "duration_s": 0.0, "project_dir": ""}

    try:
        config = load_pipeline_config("default")
    except Exception as e:
        result["error"] = f"Config loading failed: {e}"
        print(f"  [FAIL] {result['error']}")
        return result

    try:
        run_id = task["name"].lower()
        chain = ChatChain(config=config, task_prompt=task["prompt"],
                          run_id=run_id)

        start = time.time()
        project_dir = chain.run()
        elapsed = time.time() - start
        result["duration_s"] = round(elapsed, 2)

        if project_dir and os.path.isdir(project_dir):
            result["success"] = True
            result["project_dir"] = project_dir
            py_files = list((__import__("pathlib").Path(project_dir)
                             .rglob("*.py")))
            print(f"  [OK] Duration: {elapsed:.1f}s  Files: {len(py_files)}")
        else:
            result["error"] = "Project directory not found"
            print(f"  [FAIL] {result['error']}")

    except Exception as e:
        result["error"] = str(e)
        print(f"  [FAIL] {e}")

    return result


def print_summary(results: list[dict]) -> None:
    print(f"\n\n{'='*60}")
    print("  BENCHMARK SUMMARY")
    print(f"{'='*60}")
    print(f"  {'Task':<20} {'Status':<10} {'Duration':<10}")
    print(f"  {'-'*40}")

    for r in results:
        status = "PASS" if r["success"] else "FAIL"
        dur = f"{r['duration_s']:.1f}s" if r["duration_s"] else "N/A"
        print(f"  {r['name']:<20} {status:<10} {dur:<10}")

    passed = sum(1 for r in results if r["success"])
    print(f"\n  Total: {len(results)}  Passed: {passed}  "
          f"Failed: {len(results) - passed}")


def save_results(results: list[dict], path: str = "benchmark_results.json") -> None:
    summary = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(results),
        "passed": sum(1 for r in results if r["success"]),
        "results": results,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"\nResults saved to: {path}")


def main():
    print("DevForge Benchmark Runner")
    print("=" * 60)
    print(f"Tasks: {len(BENCHMARK_TASKS)}")

    results = []
    for task in BENCHMARK_TASKS:
        result = run_single_task(task)
        results.append(result)

    print_summary(results)
    save_results(results)


if __name__ == "__main__":
    main()
