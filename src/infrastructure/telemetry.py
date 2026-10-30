"""Lightweight automatic function telemetry and database query tracing.

Provides:
1. summarize_value: Smart recursive summarization preventing log explosion from large arrays.
2. @trace: Zero-overhead AOP decorator capturing inputs, outputs, execution duration, and failures.
3. wrap_traced_connection: Transparent database connection proxy capturing SQL statements, parameters,
   timings, and slow-query warnings (>15ms).
4. Rotating structured file logging (data/logs/aps_runtime.log).
"""
from __future__ import annotations

import functools
import logging
import os
import sqlite3
import time
from logging.handlers import RotatingFileHandler
from typing import Any, Callable, Dict, List, Optional


_LOGGERS: Dict[str, logging.Logger] = {}


def get_logger(name: str = "aps") -> logging.Logger:
    """Configures and returns a structured logger with both console and rotating file handlers."""
    if name in _LOGGERS:
        return _LOGGERS[name]

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    if not logger.handlers:
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

        # Console handler
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(fmt)
        logger.addHandler(ch)

        # File handler (logs/aps_runtime.log)
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        log_dir = os.path.join(base_dir, "data", "logs")
        try:
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, "aps_runtime.log")
            fh = RotatingFileHandler(log_path, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8")
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(fmt)
            logger.addHandler(fh)
        except Exception:
            pass

    _LOGGERS[name] = logger
    return logger


def summarize_value(val: Any, max_depth: int = 2) -> Any:
    """Smartly truncates and formats large collections or domain objects to avoid log explosion."""
    if val is None or isinstance(val, (int, float, bool)):
        return val
    if isinstance(val, str):
        return val if len(val) <= 80 else val[:77] + "..."
    if max_depth <= 0:
        return f"<{type(val).__name__}>"

    # Collections
    if isinstance(val, (list, tuple, set)):
        l_val = list(val)
        if len(l_val) > 4:
            sample = [summarize_value(x, max_depth - 1) for x in l_val[:2]]
            return f"[{type(val).__name__} len={len(l_val)}, sample={sample}...]"
        return [summarize_value(x, max_depth - 1) for x in l_val]

    if isinstance(val, dict):
        if len(val) > 6:
            sample_keys = list(val.keys())[:3]
            return f"Dict(keys_count={len(val)}, sample_keys={sample_keys}...)"
        return {str(k): summarize_value(v, max_depth - 1) for k, v in val.items()}

    # Domain Object Smart Extractors
    if hasattr(val, "task_id"):
        return f"Task({val.task_id}, line={getattr(val, 'line', '')}, qty={getattr(val, 'quantity', '')})"
    if hasattr(val, "status_name"):
        return f"ScheduleResult({val.status_name}, tasks={len(getattr(val, 'tasks', []))})"
    if hasattr(val, "fg_tasks") and hasattr(val, "sf_tasks"):
        return f"RequirementPlan(fg={len(val.fg_tasks)}, sf={len(val.sf_tasks)})"
    if hasattr(val, "rule_set_id"):
        return f"RuleSet({val.rule_set_id}, rules={len(getattr(val, 'rules', []))})"

    return f"<{type(val).__name__} object>"


def trace(
    stage: str = "CORE",
    capture_args: bool = True,
    capture_result: bool = True,
    logger_name: str = "aps",
) -> Callable:
    """Decorator capturing function inputs, outputs, execution duration, and errors."""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            logger = get_logger(logger_name)
            func_name = func.__qualname__
            t0 = time.perf_counter()

            if capture_args:
                clean_args = [summarize_value(a) for a in args]
                clean_kwargs = {k: summarize_value(v) for k, v in kwargs.items()}
                logger.debug(f"[{stage}] ⏩ ENTER {func_name} | args={clean_args} | kwargs={clean_kwargs}")

            try:
                result = func(*args, **kwargs)
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                res_summary = summarize_value(result) if capture_result else "<omitted>"
                logger.info(f"[{stage}] ⏹️ EXIT {func_name} | 耗时: {elapsed_ms:.2f}ms | 返回: {res_summary}")
                return result
            except Exception as e:
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                logger.error(
                    f"[{stage}] ❌ FAIL {func_name} | 耗时: {elapsed_ms:.2f}ms | 异常: {type(e).__name__}({str(e)})"
                )
                raise
        return wrapper
    return decorator


class TracedCursor:
    """Cursor proxy intercepting queries and timing execution."""

    def __init__(self, raw_cursor: sqlite3.Cursor, logger_name: str = "aps"):
        self._cur = raw_cursor
        self._logger = get_logger(logger_name)

    def execute(self, sql: str, params: Any = ()):
        t0 = time.perf_counter()
        try:
            res = self._cur.execute(sql, params)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            p_count = len(params) if isinstance(params, (list, tuple)) else (1 if params else 0)
            clean_sql = " ".join(sql.strip().split())[:90]
            log_msg = f"[DB:EXEC] {elapsed_ms:.2f}ms | SQL: {clean_sql} | params={p_count}"
            if elapsed_ms > 15.0:
                self._logger.warning(f"⚠️ [SLOW_SQL] {log_msg}")
            else:
                self._logger.debug(log_msg)
            return res
        except Exception as e:
            self._logger.error(f"[DB:ERROR] SQL: {sql[:100]} | Error: {e}")
            raise

    def executemany(self, sql: str, seq_of_params: Any):
        t0 = time.perf_counter()
        try:
            res = self._cur.executemany(sql, seq_of_params)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            clean_sql = " ".join(sql.strip().split())[:90]
            count = len(seq_of_params) if hasattr(seq_of_params, "__len__") else "?"
            self._logger.debug(f"[DB:BATCH] {elapsed_ms:.2f}ms | SQL: {clean_sql} | batch_rows={count}")
            return res
        except Exception as e:
            self._logger.error(f"[DB:ERROR] BATCH SQL: {sql[:100]} | Error: {e}")
            raise

    def executescript(self, sql_script: str):
        t0 = time.perf_counter()
        res = self._cur.executescript(sql_script)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        self._logger.debug(f"[DB:SCRIPT] {elapsed_ms:.2f}ms | script_len={len(sql_script)}")
        return res

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cur, name)

    def __iter__(self):
        return iter(self._cur)


class TracedConnection:
    """Connection proxy wrapping cursors and direct executions."""

    def __init__(self, raw_conn: sqlite3.Connection, logger_name: str = "aps"):
        self._conn = raw_conn
        self._logger_name = logger_name

    def cursor(self) -> TracedCursor:
        return TracedCursor(self._conn.cursor(), logger_name=self._logger_name)

    def execute(self, sql: str, params: Any = ()):
        return self.cursor().execute(sql, params)

    def executemany(self, sql: str, seq_of_params: Any):
        return self.cursor().executemany(sql, seq_of_params)

    def executescript(self, sql_script: str):
        return self.cursor().executescript(sql_script)

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        return self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.rollback()
        else:
            self.commit()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


def wrap_traced_connection(raw_conn: sqlite3.Connection, logger_name: str = "aps") -> TracedConnection:
    """Wraps a standard sqlite3 connection with telemetry query tracing."""
    return TracedConnection(raw_conn, logger_name=logger_name)
