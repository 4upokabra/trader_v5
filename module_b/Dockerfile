FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# shared/ is mounted at runtime via docker-compose volume
ENV PYTHONPATH="/app:/app/shared"

CMD ["python", "main.py"]
