FROM python:3.11-slim

# Prevent Python from writing pyc files and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install minimal system build tools and curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Expose Streamlit Dashboard and Industrial Simulated Ports:
# 8501: Central Security Operations Streamlit Dashboard
# 5021-5023: Modbus TCP Inverters 01, 02, 03
# 5024: Modbus TCP Substation RTU/PLC
# 2404: IEC 60870-5-104 Telecontrol Grid Link Gateway
EXPOSE 8501 5021 5022 5023 5024 2404

# Health check to ensure Streamlit is operational
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:${PORT:-8501}/_stcore/health || exit 1

# Default port (can be overridden by cloud platforms like Render/Cloud Run via $PORT)
ENV PORT=8501

# Launch the integrated service and dashboard
CMD ["sh", "-c", "streamlit run app.py --server.port=${PORT:-8501} --server.address=0.0.0.0"]
