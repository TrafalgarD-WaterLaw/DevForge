"""Web search tools — DuckDuckGo with graceful multi-engine fallback."""
import re
from urllib.parse import unquote, urlparse

from codegen.infrastructure.tools.registry import register

try:
    import requests
except ImportError:  # noqa: F401  — 工具执行时给出明确错误而非崩溃
    requests = None  # type: ignore[assignment]

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

def _ddg_instant(query: str, timeout: float = 8) -> list[str]:
    """DuckDuckGo Instant Answer API — no API key, but often sparse."""
    resp = requests.get(
        "https://api.duckduckgo.com/",
        params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
        timeout=timeout, headers={"User-Agent": _UA},
    )
    resp.raise_for_status()
    data = resp.json()
    results = []
    if data.get("AbstractText"):
        results.append(f"Summary: {data['AbstractText']}")
    for topic in data.get("RelatedTopics", [])[:3]:
        if isinstance(topic, dict) and topic.get("Text"):
            results.append(f"- {topic['Text']}")
    return results

def _ddg_html(query: str, timeout: float = 10) -> list[str]:
    """DuckDuckGo HTML search — richer results; parse with stdlib regex."""
    resp = requests.get(
        "https://html.duckduckgo.com/html/",
        params={"q": query}, timeout=timeout, headers={"User-Agent": _UA},
    )
    resp.raise_for_status()
    text = resp.text
    results = []
    # 结果标题：<a class="result__a" href="//duckduckgo.com/l/?uddg=<encoded>">Title</a>
    for m in re.finditer(
            r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            text, re.S):
        href = m.group(1)
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if not title:
            continue
        # DDG 重定向链接 → 提取真实 URL（uddg 参数）；直接链接原样保留
        target = href
        if "uddg=" in href:
            target = unquote(urlparse(href.replace("&amp;", "&"))
                             .query.split("uddg=")[-1].split("&")[0])
        results.append(f"{title} — {target}")
        if len(results) >= 5:
            break
    # 摘要：<a class="result__snippet">…</a>（部分条目有）
    if results:
        snippets = re.findall(
            r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>', text, re.S)
        for i, snip in enumerate(snippets[:len(results)]):
            clean = re.sub(r"<[^>]+>", "", snip).strip()
            if clean:
                results[i] += f"\n  {clean[:180]}"
    return results

def _bing_html(query: str, timeout: float = 10) -> list[str]:
    """Bing HTML search — DuckDuckGo 在国内被墙，Bing 可达且无需 API key。"""
    resp = requests.get(
        "https://www.bing.com/search",
        params={"q": query}, timeout=timeout, headers={"User-Agent": _UA},
    )
    resp.raise_for_status()
    text = resp.text
    results = []
    # <li class="b_algo"><h2><a href="URL">Title</a></h2>…<p>Snippet</p>
    for m in re.finditer(
            r'<li class="b_algo".*?<h2[^>]*><a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            text, re.S):
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if not title:
            continue
        results.append(f"{title} — {m.group(1)}")
        if len(results) >= 5:
            break
    if results:
        snips = re.findall(r'<li class="b_algo".*?<p[^>]*>(.*?)</p>', text, re.S)
        for i, snip in enumerate(snips[:len(results)]):
            clean = re.sub(r"<[^>]+>", "", snip).strip()
            if clean:
                results[i] += f"\n  {clean[:180]}"
    return results

@register(
    name="search_web",
    description="Search the web for documentation, solutions, or information. "
                "Use when you need up-to-date information or code examples.",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query, e.g., 'python sqlite3 create table example'"
            }
        },
        "required": ["query"]
    }
)
def search_web(query: str) -> str:
    """Perform a web search and return formatted results.

    Tries DuckDuckGo Instant Answer first (cheap), falls back to the HTML
    search page (richer results), then Bing (DuckDuckGo 在国内被墙时的兜底).
    All network failures degrade to a readable message.
    """
    if requests is None:
        return ("Search error: 'requests' is not installed — "
                "pip install requests to enable web search.")
    engines = [("Instant Answer", _ddg_instant),
               ("HTML search", _ddg_html),
               ("Bing HTML", _bing_html)]
    last_error = ""
    for label, engine in engines:
        try:
            results = engine(query)
        except Exception as e:
            last_error = f"{label}: {e}"
            continue
        if results:
            return "\n".join(results)
        last_error = f"{label}: no results"
    return (f"Search error: {last_error} — try another query "
            "or use your existing knowledge.")
