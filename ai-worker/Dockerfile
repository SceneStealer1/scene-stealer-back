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
# torchvision도 반드시 같이, 같은 인덱스에서 설치할 것 — requirements.txt에만 맡기면
# ultralytics 의존성 해석 과정에서 기본 PyPI 인덱스의 torchvision이 깔릴 수 있는데,
# 그러면 torch 빌드와 ABI가 안 맞아 "partially initialized module 'torchvision' has no
# attribute 'extension'" 순환 임포트처럼 보이는 에러로 깨진다. 버전은 아래 표를 따를 것:
# torch 2.4.1 <-> torchvision 0.19.1.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --index-url https://download.pytorch.org/whl/cpu torch==2.4.1 torchvision==0.19.1
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

COPY . .

CMD ["python", "worker.py"]
