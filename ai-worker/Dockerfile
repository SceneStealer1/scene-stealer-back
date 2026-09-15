# syntax=docker/dockerfile:1
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .

# torch를 PyPI 기본 인덱스로 설치하면 이 컨테이너엔 없는 GPU용 CUDA 런타임
# (nvidia-*-cu12 패키지들, 합쳐서 2GB+)까지 딸려와서 느린 서버에서는 사실상
# 멈춘 것처럼 보일 정도로 느려진다. CPU 전용 휠을 먼저 깔아 두면 아래
# requirements.txt 설치 시 torch==2.4.1 요구사항이 이미 충족돼서 재다운로드하지 않는다.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --index-url https://download.pytorch.org/whl/cpu torch==2.4.1
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

COPY . .

CMD ["python", "worker.py"]
