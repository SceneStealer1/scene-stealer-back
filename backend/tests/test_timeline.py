"""하루 타임라인의 '영상 없음' 구간 계산 (요구사항 4.4).

2e 타임라인이 공백을 점선으로 정직하게 그리려면, 조각이 안 올라온 구간을
서버가 알려줘야 한다. 이게 이 모듈의 존재 이유다.
"""

from datetime import datetime, timedelta, timezone

from app.domain.timeline import compute_gaps

UTC = timezone.utc


def t(hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 9, 16, hour, minute, second, tzinfo=UTC)


class TestComputeGaps:
    def test_no_coverage_is_one_gap_spanning_the_window(self):
        assert compute_gaps([], t(0), t(24 - 1, 59, 59)) == [(t(0), t(23, 59, 59))]

    def test_full_coverage_has_no_gaps(self):
        assert compute_gaps([(t(0), t(23))], t(0), t(23)) == []

    def test_single_hole_between_two_segments(self):
        covered = [(t(9), t(10)), (t(11), t(12))]
        assert compute_gaps(covered, t(9), t(12)) == [(t(10), t(11))]

    def test_gap_before_first_and_after_last(self):
        covered = [(t(10), t(11))]
        assert compute_gaps(covered, t(9), t(12)) == [(t(9), t(10)), (t(11), t(12))]

    def test_overlapping_segments_are_merged(self):
        # 카메라가 재연결되면서 경계가 겹치는 조각이 올라올 수 있다.
        covered = [(t(9), t(10, 30)), (t(10), t(11))]
        assert compute_gaps(covered, t(9), t(12)) == [(t(11), t(12))]

    def test_unsorted_input_is_handled(self):
        covered = [(t(11), t(12)), (t(9), t(10))]
        assert compute_gaps(covered, t(9), t(12)) == [(t(10), t(11))]

    def test_tolerance_ignores_sub_second_seams(self):
        # 조각 경계는 벽시계 정렬이라 밀리초 단위로 어긋난다. 그걸 공백이라고
        # 부르면 타임라인이 점선으로 뒤덮인다.
        covered = [(t(9), t(10)), (t(10, 0, 1), t(11))]
        assert compute_gaps(covered, t(9), t(11), tolerance_sec=2) == []

    def test_tolerance_does_not_swallow_real_gaps(self):
        covered = [(t(9), t(10)), (t(10, 0, 30), t(11))]
        assert compute_gaps(covered, t(9), t(11), tolerance_sec=2) == [(t(10), t(10, 0, 30))]

    def test_coverage_outside_window_is_clipped(self):
        covered = [(t(8), t(9, 30))]
        assert compute_gaps(covered, t(9), t(11)) == [(t(9, 30), t(11))]

    def test_zero_length_window_has_no_gaps(self):
        assert compute_gaps([], t(9), t(9)) == []

    def test_segment_entirely_outside_window_is_ignored(self):
        assert compute_gaps([(t(1), t(2))], t(9), t(10)) == [(t(9), t(10))]

    def test_minimum_gap_filters_noise(self):
        # 5초짜리 공백을 UI에 그려 봐야 픽셀 하나다. 최소 길이 아래는 버린다.
        covered = [(t(9), t(10)), (t(10, 0, 5), t(11))]
        gaps = compute_gaps(covered, t(9), t(11), tolerance_sec=0, min_gap_sec=10)
        assert gaps == []

    def test_gap_equal_to_minimum_is_kept(self):
        covered = [(t(9), t(10)), (t(10, 0, 10), t(11))]
        gaps = compute_gaps(covered, t(9), t(11), tolerance_sec=0, min_gap_sec=10)
        assert gaps == [(t(10), t(10, 0, 10))]


class TestRealisticDay:
    def test_camera_disconnected_for_thirteen_minutes(self):
        """모바일 2k 의 '창고 끊김 13분' 이 나오는 경로."""
        start = t(9)
        covered = []
        cursor = start
        for _ in range(60):  # 1분짜리 조각 60개 = 09:00~10:00
            covered.append((cursor, cursor + timedelta(minutes=1)))
            cursor += timedelta(minutes=1)
        cursor += timedelta(minutes=13)  # 끊김
        for _ in range(10):
            covered.append((cursor, cursor + timedelta(minutes=1)))
            cursor += timedelta(minutes=1)

        gaps = compute_gaps(covered, start, cursor, tolerance_sec=2)
        assert gaps == [(t(10), t(10, 13))]
        assert (gaps[0][1] - gaps[0][0]).total_seconds() == 13 * 60
