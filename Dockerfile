FROM python:3.11-slim

WORKDIR /app

# Prevent Python from writing .pyc files and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose web dashboard port
EXPOSE 5000

# Default configuration
ENV FLASK_HOST=0.0.0.0
ENV FLASK_PORT=5000
ENV AWS_REGION=us-east-1

# Start the unified single-process entry point (API + pipeline)
CMD ["python", "app.py"]
