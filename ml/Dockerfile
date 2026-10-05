FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# utilisateur non privilegie (uid 1000 = proprietaire du depot, pour ecrire models/ en reentrainement)
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin sentinel
COPY --chown=sentinel:sentinel . .
USER sentinel

CMD ["python", "service.py"]
