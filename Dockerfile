# Hugging Face Spaces (Docker SDK, free CPU). Also works on Render, Railway, Fly.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PORT=7860
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Spaces run as a non-root user: the app writes its cache and new dossiers here
RUN mkdir -p .cache data/research && chmod -R 777 .cache data
EXPOSE 7860
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
