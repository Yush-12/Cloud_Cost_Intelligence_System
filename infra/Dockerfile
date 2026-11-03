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

# Expose the offline demo port
EXPOSE 5002

# Default entry point runs the demo costintel dashboard in demo mode
CMD ["python", "-m", "costintel", "serve", "--mode", "demo", "--port", "5002", "--host", "0.0.0.0", "--public-demo"]
