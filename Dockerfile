# Dockerfile for Python Flask Face Recognition & Smart Attendance
FROM python:3.10-slim-bookworm

# Prevent interactive prompts
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# CRITICAL FOR RENDER BUILD MEMORY LIMIT:
# Restrict C++ compiler and CMake to 1 single thread to prevent out-of-memory errors
ENV CMAKE_BUILD_PARALLEL_LEVEL=1
ENV MAKEFLAGS="-j1"

# Install pre-compiled system dlib, OpenBLAS, OpenCV dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    libopenblas-dev \
    liblapack-dev \
    python3-dlib \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Link system-installed python3-dlib so Python finds the pre-compiled binary instantly
ENV PYTHONPATH="/usr/lib/python3/dist-packages:${PYTHONPATH}"

WORKDIR /app

# Copy dependency definition
COPY requirements.txt .

# Install dependencies (dlib is already available via system, so no heavy compilation needed)
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Ensure data and upload directories exist
RUN mkdir -p data/photos static

# Expose default port
EXPOSE 5000

# Start Flask with Gunicorn (binds to $PORT environment variable automatically on Render / Railway / Cloud)
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-5000} --workers 1 --timeout 120 app:app"]
