FROM python:3.12-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl gcc && \
    rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install additional deps for file processing
RUN pip install --no-cache-dir pymupdf python-docx

# Copy application
COPY . .

# Create directories for uploads and data
RUN mkdir -p /app/uploads/kp /app/data/chromadb

# Default port
ENV PORT=5050
EXPOSE 5050

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:5050/health || exit 1

# Run with gunicorn
CMD ["python", "main.py", "serve"]
