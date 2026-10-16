FROM python:3.12-slim
RUN pip install --no-cache-dir "aiohttp==3.10.*" "aiomqtt==2.3.*"
WORKDIR /app
COPY --chmod=644 bridge.py .
USER nobody
CMD ["python", "-u", "bridge.py"]
