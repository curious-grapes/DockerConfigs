FROM python:3.12-slim

WORKDIR /app

# Only the two packages we need
RUN pip install --no-cache-dir aiohttp[speedups]

COPY app.py .

EXPOSE 8034

CMD ["python", "-u", "app.py"]