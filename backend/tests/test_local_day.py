"""매장 현지 하루의 경계 (계약 5.1 · 5.5 의 date 파라미터).

사장님에게 '오늘'은 UTC 하루가 아니다. 한국 매장에서 UTC 로 자르면 자정~오전 9시
이벤트(새벽 노숙·취침 같은 것)가 전날 기록으로 빠진다.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from app.domain.local_day import local_day_bounds, local_today

SEOUL = ZoneInfo("Asia/Seoul")
UTC = timezone.utc


class TestLocalDayBounds:
    def test_seoul_day_starts_at_previous_utc_15h(self):
        start, end = local_day_bounds("2026-09-16", SEOUL)
        assert start == datetime(2026, 9, 15, 15, 0, tzinfo=UTC)
        assert end == datetime(2026, 9, 16, 15, 0, tzinfo=UTC)

    def test_dawn_event_belongs_to_the_local_day(self):
        """새벽 2시 10분 노숙 이벤트 — 2e 디자인의 02:10 행."""
        start, end = local_day_bounds("2026-09-16", SEOUL)
        dawn = datetime(2026, 9, 16, 2, 10, tzinfo=SEOUL)
        assert start <= dawn < end

    def test_utc_zone_is_plain_midnight(self):
        start, end = local_day_bounds("2026-09-16", UTC)
        assert start == datetime(2026, 9, 16, 0, 0, tzinfo=UTC)
        assert end == datetime(2026, 9, 17, 0, 0, tzinfo=UTC)

    def test_day_is_not_always_24_hours(self):
        """서머타임이 있는 곳에서는 하루가 23·25시간이다. +24h 로 계산하면 경계가 밀린다."""
        new_york = ZoneInfo("America/New_York")
        start, end = local_day_bounds("2026-03-08", new_york)  # 미국 서머타임 시작일
        assert (end - start).total_seconds() == 23 * 3600

    @pytest.mark.parametrize("bad", ["2026-13-01", "16-09-2026", "", "오늘"])
    def test_invalid_date_is_400(self, bad):
        with pytest.raises(HTTPException) as caught:
            local_day_bounds(bad, SEOUL)
        assert caught.value.status_code == 400


class TestLocalToday:
    def test_today_follows_the_store_clock(self):
        # UTC 로는 9월 15일 20시지만 서울은 이미 16일 새벽 5시다.
        now = datetime(2026, 9, 15, 20, 0, tzinfo=UTC)
        start, _ = local_day_bounds("2026-09-16", SEOUL)
        assert local_today(SEOUL, now) == start
