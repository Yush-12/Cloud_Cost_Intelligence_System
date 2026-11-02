FROM python:3.12-slim

WORKDIR /app

# Prevent Python from writing .pyc files and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy entire repository
COPY . .

# Expose web dashboard ports (5002 for costintel, 5000 for simulation)
EXPOSE 5002 5000

# Default entry point runs the production-grade costintel dashboard in demo mode
CMD ["python", "-m", "costintel", "serve", "--mode", "demo", "--port", "5002"]
