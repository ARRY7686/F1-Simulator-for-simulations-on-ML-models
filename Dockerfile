FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source files
COPY fetch_data.py serve.py simulator.html build_tracks.py track_layouts.json ./

# Persistent cache directories (mount as Render Disk at /app/cache and /app/race_cache)
RUN mkdir -p cache race_cache

EXPOSE 10000

CMD ["python", "serve.py"]
