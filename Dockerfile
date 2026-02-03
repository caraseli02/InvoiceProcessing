FROM python:3.12-slim as builder

RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-ron \
    tesseract-ocr-eng \
    tesseract-ocr-rus \
    libtesseract-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-ron \
    tesseract-ocr-eng \
    tesseract-ocr-rus \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /root/.local /root/.local

COPY . .

RUN pip install --no-cache-dir -e .

RUN mkdir -p output/grids output/ocr_debug output/results

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["python", "-m", "invproc", "--mode", "api"]
