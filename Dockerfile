FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
# No .env, session, Telegram keys or downloaded media copied into the image.
COPY app.py relay_api.py cloud_queue.py install_progress_cleanup.py one_command_deploy.py install_resume_backend.py ./
# Cloud queue/previous patches first, then safe resumable browser relay patch.
RUN python3 one_command_deploy.py --build-mode \
    && python3 install_resume_backend.py relay_api.py \
    && python3 -m py_compile app.py relay_api.py cloud_queue.py
ENV PYTHONUNBUFFERED=1 PORT=8080
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:${PORT}/health || exit 1
CMD ["sh", "-c", "exec gunicorn --workers 1 --threads 8 --timeout 600 --bind 0.0.0.0:${PORT:-8080} app:app"]
