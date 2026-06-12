FROM python:3.12-slim

# System dependencies for Cartopy, Py-ART, and geopandas
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libgeos-dev \
    libproj-dev \
    proj-data \
    proj-bin \
    libhdf5-dev \
    libnetcdf-dev \
    libeccodes-dev \
    libgdal-dev \
    gdal-bin \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scripts/ ./scripts/
RUN python scripts/download_cartopy.py

# Copy source code
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY events/ ./events/
COPY .env.example ./.env.example
COPY tailwind.config.js ./tailwind.config.js

# Cache and output dirs, these will be mounted as volumes in production
RUN mkdir -p .cache output

# Expose port
EXPOSE 8000

CMD ["uvicorn", "src.web.app:app", "--host", "0.0.0.0", "--port", "8000"]