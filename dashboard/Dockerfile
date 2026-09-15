FROM python:3.13-slim-bookworm@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e

WORKDIR /app
COPY simulator/requirements.txt ./simulator/requirements.txt
COPY dashboard/requirements.txt ./dashboard/requirements.txt
RUN pip install --no-cache-dir -r dashboard/requirements.txt
COPY simulator/__init__.py simulator/config.py simulator/database.py ./simulator/
COPY dashboard/*.py ./dashboard/
COPY dashboard/sql ./dashboard/sql
COPY dashboard/templates ./dashboard/templates
COPY dashboard/static ./dashboard/static
ENV PYTHONDONTWRITEBYTECODE=1
USER 10001:10001
ENTRYPOINT ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "dashboard.app:create_app()"]
