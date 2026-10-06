FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Code appartenant à root, lisible seulement par l'utilisateur applicatif (non-root, sans shell)
RUN useradd --uid 1000 --no-create-home --shell /usr/sbin/nologin sentinel \
 && chmod -R a-w,a+rX /app
USER sentinel

EXPOSE 3000

# --no-server-header : pas de bannière "uvicorn" ; keep-alive court contre les connexions lentes
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3000", "--no-server-header", "--timeout-keep-alive", "5", "--limit-concurrency", "200"]
