FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# sqlite3 useful for debugging inside the container
RUN apt-get update && apt-get install -y sqlite3 && rm -rf /var/lib/apt/lists/*

# Railway volume is mounted at /data
RUN mkdir -p /data

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "bot.py"]
