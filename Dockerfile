FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Python deps. constraints.txt pins the whole resolved tree, so rebuilding an
# unchanged commit installs the same versions instead of re-resolving.
COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt

# App code
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY config.yaml .

# Data directories
RUN mkdir -p /app/data/pdfs /app/data/xmls

# Streamlit config
COPY .streamlit/config.toml /root/.streamlit/config.toml

EXPOSE 8501

CMD ["streamlit", "run", "app/web/app.py"]
