# ai-worker

`ingest-worker` 가 Supabase Storage `videos` 버킷에 올리고 `videos` 테이블에
`status='uploaded'` 로 만든 행을 폴링해서 분석하는 워커.

`scene-stiller/worker` 에 있던 분석 파이프라인(YOLO11-pose + ByteTrack 포즈 추출 →
오토인코더 이상행동 탐지 → ffmpeg 클립/썸네일 추출)을 그대로 이관했다.

## 하이라이트 클립

이상 구간은 `pipeline/report.py` 의 `HIGHLIGHT_PAD_SEC = 5.0` 만큼 앞뒤로 패딩해서
뽑는다 — 대시보드에서 무슨 일이 있었는지 맥락을 보려면 탐지 구간만으로는 부족해서다.
(`clip_export.extract_clip` 자체의 기본 패딩 1초는 키프레임 seek 오차 보정용으로,
별개의 용도라 여기서 명시적으로 5초로 덮어쓴다.)

`anomaly_events` 에 채워지는 값:

| 컬럼 | 의미 |
|---|---|
| `start_time_sec` / `end_time_sec` | 원본 5분 조각 기준 상대 시간 (패딩 전, 탐지된 구간 그대로) |
| `anomaly_score` / `threshold` | 오토인코더 재구성 오차와 판정 임계값 |
| `clip_storage_path` | Storage `clips` 버킷 경로 — 패딩 포함된 실제 클립 파일 |
| `thumbnail_storage_path` | 구간 중간 지점 썸네일 |

실제 촬영 절대시각은 `videos.recorded_started_at` + `start_time_sec`/`end_time_sec`
오프셋으로 backend 조회 API가 계산한다 — 여기서 절대시각을 따로 저장하지 않는다.

## 로컬 실행

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # SUPABASE_URL / SUPABASE_SERVICE_KEY 채우기
python worker.py
```

GPU 없이 CPU로도 돌아가지만(포즈 모델이 `yolo11n-pose.pt` 나노 버전), 5분 영상
처리에 CPU 기준 수 분이 걸릴 수 있다 — ingest-worker 는 응답을 기다리지 않고
바로 반환하므로 이 워커가 느려도 업로드 자체는 막히지 않는다.

## 배포

이 폴더가 아니라 저장소 루트(`scene-stealer-back/`)에서 `./build-deploy.sh`
로 `ingest-worker`/`backend`/`nginx` 와 함께 한 스택으로 올라간다.
