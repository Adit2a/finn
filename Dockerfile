# Deployed on Render (free web service, see render.yaml). Also runs on Railway, Fly or any Docker host.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PORT=10000
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# the app writes its cache and newly built dossiers here
RUN mkdir -p .cache data/research && chmod -R 777 .cache data
EXPOSE 10000
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
