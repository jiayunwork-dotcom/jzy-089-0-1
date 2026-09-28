FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY seed ./seed

RUN mkdir -p /data

# 前端静态产物由独立的 frontend 容器（Nginx）托管；
# 若把 dist 挂载/复制到 $FRONTEND_DIST，后端也可直接托管。
ENV FRONTEND_DIST=/app/frontend_dist
EXPOSE 8000

CMD ["python", "-m", "app.main"]
