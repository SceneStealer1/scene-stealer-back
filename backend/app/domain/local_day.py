"""매장 현지 하루의 경계.

화면의 '오늘', 2e 의 날짜 넘기기, 하루 타임라인은 전부 매장 벽시계 기준이다.
UTC 자정으로 자르면 한국 매장의 자정~오전 9시 이벤트가 전날로 빠진다.
"""

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import HTTPException


def local_day_bounds(day: str, zone: ZoneInfo | timezone) -> tuple[datetime, datetime]:
    """'2026-09-16' → 그 현지 날짜의 [시작, 끝) 을 UTC 로.

    끝은 +24시간이 아니라 '다음 날 현지 자정'이다 — 서머타임이 있는 곳에서는
    하루가 23·25시간이다.
    """
    try:
        parsed = date.fromisoformat(day)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=f"날짜 형식이 올바르지 않습니다 (YYYY-MM-DD): {day}") from error

    start = datetime.combine(parsed, time.min, tzinfo=zone)
    end = datetime.combine(parsed + timedelta(days=1), time.min, tzinfo=zone)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def local_today(zone: ZoneInfo | timezone, now: Optional[datetime] = None) -> datetime:
    """매장 현지 '오늘'이 시작된 시각 (UTC)."""
    moment = (now or datetime.now(timezone.utc)).astimezone(zone)
    return local_day_bounds(moment.date().isoformat(), zone)[0]
