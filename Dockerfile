# Dockerfile for Python Flask Face Recognition & Smart Attendance
FROM python:3.10-slim

# Prevent interactive prompts
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Install runtime libraries required by OpenCV
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Upgrade pip
RUN pip install --no-cache-dir --upgrade pip

# Copy requirements (includes dlib-bin precompiled wheel, face_recognition_models, etc.)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install face_recognition without dependencies so pip will NOT attempt to compile dlib from source
RUN pip install --no-cache-dir --no-deps face_recognition

# Copy all project files
COPY . .

# Ensure data and upload directories exist
RUN mkdir -p data/photos static

# Expose default port
EXPOSE 5000

# Start Flask with Gunicorn (binds to $PORT environment variable on Render)
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-5000} --workers 1 --timeout 120 app:app"]
