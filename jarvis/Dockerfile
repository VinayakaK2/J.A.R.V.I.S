FROM python:3.10-slim

# Install system dependencies required for GUI automation (if running graphically in docker)
# Given JARVIS intercepts OS layers, Playwright requires these libraries
RUN apt-get update && apt-get install -y \
    wget gnupg xvfb libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 \
    libxfixes3 libxrandr2 libgbm1 libasound2 tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install playwright mss Pillow pyjwt passlib bcrypt

# Install Playwright dependencies explicitly for chromium
RUN playwright install chromium
RUN playwright install-deps

# Copy the rest of the backend codebase
COPY . .

# Expose FastAPI port
EXPOSE 8000

# Start server natively
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
