FROM python:3.10-slim

WORKDIR /app

# Instala dependências
RUN pip install --no-cache-dir fastapi uvicorn httpx aiofiles

# Copia tudo (incluindo a pasta static que criamos)
COPY . .

# Cria diretório de dados se não existir
RUN mkdir -p /app/data

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]