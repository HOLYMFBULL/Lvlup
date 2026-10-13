# ========================================================
# SAITO Control — Free Fire Level Up Bot Dockerfile
# Ready for Render Web Service (Docker Environment)
# ========================================================
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PORT=5000

# Install build tools needed for pycryptodome & socket headers
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python requirements
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy all code files
COPY . ./

# Render standard web service port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:5000/api/stats || exit 1

# Start the bot automation engine and aiohttp web server
CMD ["python", "main.py"]
