FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# Slow/unstable links to PyPI often default-timeout; bump for Docker builds on poor networks
ENV PIP_DEFAULT_TIMEOUT=300

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY fin_savvy_app/requirements.txt /app/requirements.txt

RUN pip install --upgrade pip && \
    pip install --no-cache-dir --retries 8 --timeout 300 -r /app/requirements.txt

COPY fin_savvy_app /app/fin_savvy_app

EXPOSE 8000

CMD ["uvicorn", "fin_savvy_app.main:app", "--host", "0.0.0.0", "--port", "8000"]
