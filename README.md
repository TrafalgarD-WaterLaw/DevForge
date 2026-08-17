<div align="center">

# DevForge — AI Multi-Agent Collaborative Software Forge

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-green.svg)](https://opensource.org/licenses/Apache-2.0)
[![Status: Alpha](https://img.shields.io/badge/status-alpha-orange.svg)]()

**Empowering AI agents to collaboratively design, code, review, and test software — from a single prompt.**

</div>

---

## Overview

DevForge is an open-source framework that orchestrates multiple AI agents (CEO, Developer, Reviewer, QA) to build software collaboratively. Given a natural-language task description, DevForge runs a pipeline of phases — demand analysis, coding, code review, testing — producing runnable code in a structured project directory.

Built on a modular four-layer architecture (Providers, Agents, Pipeline, Tools+Memory), DevForge supports multiple LLM backends, extensible tools, and configurable pipelines.

---

## Features

- **Multi-Agent Collaboration** — Four specialized agents (CEO, Developer, Reviewer, QA) work together in a structured pipeline
- **Pluggable LLM Providers** — DeepSeek, OpenAI, Qwen, or custom backends via a unified Provider abstraction
- **Flexible Pipeline Engine** — Serial, parallel, conditional, and loop execution patterns with YAML-defined workflows
- **Code Quality Evaluation** — Six-dimensional scoring (syntax, runnability, completeness, lint, complexity, test coverage)
- **Vector Memory** — ChromaDB-powered retrieval for persistent agent context across sessions
- **Web Dashboard** — Real-time pipeline visualization, history, and project inspection via FastAPI + Vue 3
- **Tool System** — Decorator-based tool registration with code execution, file I/O, git, and web searching
- **Incremental Development** — Build on existing codebases with the incremental development mode

---

## Quick Start

### Prerequisites

- Python 3.10+
- An LLM API key (DeepSeek, OpenAI, or Qwen)

### Installation

```bash
git clone https://github.com/your-org/devforge.git
cd devforge
pip install -r requirements.txt
```

### Configuration

Set your API key as an environment variable or edit `devforge.yaml`:

```bash
export DEEPSEEK_API_KEY="your-api-key-here"
```

### Run

```bash
python cli.py --task "Design a calculator app with a CLI interface"

# With custom project name and pipeline
python cli.py --task "Build a todo app" --name TodoApp --pipeline default
```

### Output

Generated code appears in `WareHouse/<ProjectName>_<Org>_<Timestamp>/`.

---

## Architecture

```
┌────────────────────────────────────────────────────────────┐
│                    Tools + Memory Layer                     │
│  Code Tools | File Tools | Git Tools | Web Tools | Memory  │
├────────────────────────────────────────────────────────────┤
│                     Pipeline Layer                          │
│  Serial Executor | Parallel Executor | Condition | Loop    │
│  Phases: DemandAnalysis → Coding → Review → Test           │
├────────────────────────────────────────────────────────────┤
│                      Agents Layer                           │
│  CEO (analyst) | Developer (coder) | Reviewer | QA Engineer│
├────────────────────────────────────────────────────────────┤
│                     Providers Layer                         │
│  DeepSeek | OpenAI | Qwen | (Custom Provider)              │
└────────────────────────────────────────────────────────────┘
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full architecture documentation.

---

## Project Structure

```
devforge/
├── agents/          # Agent definitions and role-playing logic
├── config/          # YAML config loader with env var substitution
├── evaluator/       # Six-dimensional code quality evaluation
├── memory/          # ChromaDB vector memory store & retrieval
├── pipeline/        # Pipeline engine, phases, and executors
├── providers/       # LLM provider abstraction layer
├── server/          # FastAPI web server with WebSocket support
├── tools/           # Decorator-based tool registration system
web/                 # Vue 3 frontend (dashboard + animation)
configs/             # Pipeline, phase, and role YAML configs
tests/               # Unit tests and benchmark runner
docs/                # Architecture and comparison documentation
```

---

## Documentation

- [Architecture](docs/ARCHITECTURE.md) — Four-layer architecture, design patterns, extension guide
- [Comparison](docs/COMPARISON.md) — DevForge vs AutoGen vs MetaGPT vs CrewAI
- [Benchmark](tests/benchmark.py) — Run your own benchmarks

---

## License

[Apache License 2.0](LICENSE)

---

## Acknowledgments

Built on the CAMEL role-playing framework and inspired by ChatDev's multi-agent collaboration paradigm.

## 运行提示

- 像素办公室素材（背景图/角色小人）为本地持有资源，未随仓库分发（版权未知）。
  本地运行不受影响：`web/public/sprites/` 保留在项目目录即可。
