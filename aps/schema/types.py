from __future__ import annotations

import datetime as dt
import re
from enum import Enum
from typing import Any

__all__ = [
    "BOOL_TRUE",
    "BOOL_FALSE",
    "ContractTier",
    "DataType",
    "DupPolicy",
    "ParseError",
    "SHIFT_NAME_TO_INDEX",
    "parse_enum_code",
    "parse_scalar",
    "parse_shift_index",
]


class DataType(str, Enum):
    TEXT = "TEXT"
    INT = "INT"
    REAL = "REAL"
    BOOL = "BOOL"
    DATE = "DATE"
    TIME = "TIME"
    DATETIME = "DATETIME"
    MULTI_TEXT = "MULTI_TEXT"


class ContractTier(str, Enum):
    T1 = "T1"
    T2 = "T2"


class DupPolicy(str, Enum):
    ERROR = "error"
    SUM = "sum"
    DEDUPE = "dedupe"


class ParseError(ValueError):
    def __init__(self, message: str, raw: Any) -> None:
        super().__init__(message)
        self.raw = raw


BOOL_TRUE = {"true", "1", "yes", "y", "是"}
BOOL_FALSE = {"false", "0", "no", "n", "否"}

SHIFT_NAME_TO_INDEX = {"早班": 0, "白班": 0, "中班": 1, "晚班": 2, "夜班": 1}

_PAREN_RE = re.compile(r"^(?P<outer>[^()]*)\((?P<inner>[^()]*)\)\s*$")
_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d")
_TIME_FORMATS = ("%H:%M:%S", "%H:%M")
_DATETIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M")


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _to_int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            raise ParseError(f"期望整数，得到非整数浮点 {value!r}", value)
        return int(value)
    text = str(value).strip()
    try:
        return int(text)
    except ValueError:
        pass
    try:
        number = float(text)
        if not number.is_integer():
            raise ParseError(f"期望整数，得到非整数浮点 {value!r}", value)
        return int(number)
    except ValueError:
        pass
    match = re.search(r"[-+]?\d+", text)
    if match:
        return int(match.group(0))
    raise ParseError(f"无法解析为整数: {value!r}", value) from None


def _to_real(value: Any) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except ValueError:
        raise ParseError(f"无法解析为实数: {value!r}", value) from None


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value in (0, 1):
            return bool(value)
        raise ParseError(f"无法解析为布尔: {value!r}", value)
    text = str(value).strip().lower()
    if text in BOOL_TRUE:
        return True
    if text in BOOL_FALSE:
        return False
    raise ParseError(f"无法解析为布尔: {value!r}", value)


def _from_formats(text: str, formats: tuple[str, ...], value: Any) -> Any:
    for fmt in formats:
        try:
            return dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ParseError(f"无法解析日期/时间: {value!r}", value)


def _to_date(value: Any) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return _from_formats(str(value).strip(), _DATE_FORMATS, value).date()


def _to_time(value: Any) -> dt.time:
    if isinstance(value, dt.datetime):
        return value.time()
    if isinstance(value, dt.time):
        return value
    clean = str(value).strip().replace("：", ":")
    return _from_formats(clean, _TIME_FORMATS, value).time()


def _to_datetime(value: Any) -> dt.datetime:
    if isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.date):
        return dt.datetime(value.year, value.month, value.day)
    text = str(value).strip().replace("：", ":")
    for fmt in _DATETIME_FORMATS + _DATE_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ParseError(f"无法解析为日期时间: {value!r}", value)


def _to_multi_text(value: Any) -> tuple[str, ...]:
    if isinstance(value, (list, tuple, set)):
        items = [str(v).strip() for v in value]
    else:
        items = [p.strip() for p in re.split(r"[,，]", str(value))]
    return tuple(p for p in items if p)


def parse_scalar(value: Any, dtype: DataType) -> Any:
    if _is_blank(value):
        return None
    if dtype is DataType.TEXT:
        return str(value).strip()
    if dtype is DataType.INT:
        return _to_int(value)
    if dtype is DataType.REAL:
        return _to_real(value)
    if dtype is DataType.BOOL:
        return _to_bool(value)
    if dtype is DataType.DATE:
        return _to_date(value)
    if dtype is DataType.TIME:
        return _to_time(value)
    if dtype is DataType.DATETIME:
        return _to_datetime(value)
    if dtype is DataType.MULTI_TEXT:
        return _to_multi_text(value)
    raise ParseError(f"未知数据类型 {dtype!r}", value)


def _is_ascii(text: str) -> bool:
    return all(ord(ch) < 128 for ch in text)


def parse_enum_code(value: Any) -> str | None:
    if _is_blank(value):
        return None
    text = str(value).strip()
    match = _PAREN_RE.match(text)
    if match:
        outer = match.group("outer").strip()
        inner = match.group("inner").strip()
        if _is_ascii(inner) and inner:
            return inner
        if _is_ascii(outer) and outer:
            return outer
        return inner or outer or None
    if _is_ascii(text):
        return text
    return None


def parse_shift_index(value: Any) -> int | None:
    if _is_blank(value):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    return SHIFT_NAME_TO_INDEX.get(str(value).strip())
