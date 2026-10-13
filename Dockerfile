# Dockerfile for Python Flask Face Recognition & Smart Attendance
FROM python:3.10-slim

# Prevent interactive prompts
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Install runtime libraries needed by OpenCV
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Upgrade pip and install precompiled dlib binary wheel
# This completely prevents C++ compiling from source and prevents the 8GB RAM OOM error!
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir dlib-bin

# Copy requirements and install remaining packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Ensure data and upload directories exist
RUN mkdir -p data/photos static

# Expose default port
EXPOSE 5000

# Start Flask with Gunicorn (binds to $PORT environment variable on Render)
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-5000} --workers 1 --timeout 120 app:app"]
