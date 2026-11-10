FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements-hosted.txt .
RUN pip install --no-cache-dir -r requirements-hosted.txt && useradd -r -u 10001 vivamente
COPY core.py postgres_store.py hosted.py schema.sql login.html interface.html ./
COPY assets/ ./assets/
USER 10001
EXPOSE 8000
CMD ["gunicorn","--bind","0.0.0.0:8000","--workers","2","--threads","3","--timeout","35","hosted:from_env()"]
