# One image for both the API and the Streamlit UI (docker-compose runs it twice with different commands).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# Download the embedding model at build time, so the container never downloads at runtime.
RUN python -c "from chromadb.utils.embedding_functions import DefaultEmbeddingFunction as E; E()(['warm up'])"

COPY app ./app
COPY scripts ./scripts
COPY ui ./ui
COPY .streamlit ./.streamlit
COPY data ./data
COPY evaluation ./evaluation

ENV DATA_DIR=/data DOCUMENTS_DIR=/app/data/documents
EXPOSE 8000 8501
CMD ["sh", "scripts/start_api.sh"]
