FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN groupadd --system huntloop && useradd --system --gid huntloop --home-dir /app huntloop \
    && chown -R huntloop:huntloop /app

USER huntloop

CMD ["python", "main.py"]
