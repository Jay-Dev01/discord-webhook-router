FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ROUTER_CONFIG_PATH=/data/config.json

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY bot.py routing.py config_store.py ./
RUN mkdir -p /data

CMD ["python", "bot.py"]
