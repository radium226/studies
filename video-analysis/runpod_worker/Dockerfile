FROM nvidia/cuda:12.8.1-cudnn-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip \
    libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir \
    websockets \
    deepface \
    tf-keras \
    opencv-python-headless \
    numpy

COPY server.py /app/server.py

EXPOSE 8765

CMD ["python3", "/app/server.py"]
