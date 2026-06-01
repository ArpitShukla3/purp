FROM python:3.11-slim AS base

# Prevent Python from buffering stdout/stderr
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install dependencies first (layer caching)
COPY detection/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared library then service code
COPY shared/ ./shared/
COPY detection/ ./detection/

# Default command
CMD ["python", "-m", "detection.main"]
