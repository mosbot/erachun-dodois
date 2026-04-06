FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY app/ ./app/
COPY config.yaml .

# Data directories
RUN mkdir -p /app/data/pdfs /app/data/xmls

# Streamlit config
COPY .streamlit/config.toml /root/.streamlit/config.toml

EXPOSE 8501

CMD ["streamlit", "run", "app/web/app.py"]
