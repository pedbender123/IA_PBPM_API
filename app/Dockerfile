FROM python:3.10-slim
WORKDIR /app

# Instala dependências
RUN pip install --no-cache-dir fastapi uvicorn httpx

COPY main.py .

# Cria diretório para dados
RUN mkdir -p /app/data

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]