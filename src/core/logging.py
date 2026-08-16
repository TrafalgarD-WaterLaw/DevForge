"""Structured logging — run-id threaded through every log record.

Pipeline runs execute in worker threads where the run id lives in a
contextvar; the logging filter reads it so all ``devforge.*`` records
carry ``[run=xxxx]``, making a single run's full lifecycle greppable.
"""
import logging

from core.context import get_current_run

class RunIdFilter(logging.Filter):
    """Attach the active run id to every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.run_id = get_current_run() or "-"
        except Exception:
            record.run_id = "-"
        return True

_FORMAT = ("%(asctime)s %(levelname)-7s [run=%(run_id)s] "
           "%(name)s: %(message)s")

def configure_logging(level: int = logging.INFO):
    """Configure root logging with the run-id filter (idempotent-ish)."""
    root = logging.getLogger()
    if root.handlers and any(getattr(h, "devforge_filtered", False)
                             for h in root.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_FORMAT))
    handler.devforge_filtered = True
    handler.addFilter(RunIdFilter())
    root.handlers = [handler]
    root.setLevel(level)
