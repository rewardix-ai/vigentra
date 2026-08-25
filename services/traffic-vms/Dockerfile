# Traffic VMS - mock source system #1.
# Build context is the repository root so the shared synthetic clips in
# data/videos/traffic can be baked in without duplicating them per service.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY services/traffic-vms/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY services/traffic-vms/app ./app
COPY services/traffic-vms/data ./data
COPY data/videos/traffic ./videos

RUN useradd --create-home --uid 10001 vms && chown -R vms:vms /app
USER vms

EXPOSE 8001

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=2).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
