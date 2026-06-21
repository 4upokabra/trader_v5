FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY overlay_service.py .

CMD ["python", "overlay_service.py"]
