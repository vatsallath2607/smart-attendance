# Dockerfile for Python Flask Face Recognition & Smart Attendance
FROM python:3.10-slim

# Prevent interactive prompts
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Install system dependencies required for cmake, dlib, opencv, and openblas
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    libopenblas-dev \
    liblapack-dev \
    libx11-dev \
    libgtk-3-dev \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency definition
COPY requirements.txt .

# Install dependencies including gunicorn for production
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Copy project files
COPY . .

# Ensure data and upload directories exist
RUN mkdir -p data/photos static

# Expose default port
EXPOSE 5000

# Start Flask with Gunicorn (binds to $PORT environment variable automatically on Render / Railway / Cloud)
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-5000} --workers 2 --timeout 120 app:app"]
