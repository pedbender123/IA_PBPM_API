FROM python:3.10-slim
WORKDIR /app
# Instala com --no-cache para manter a imagem leve
RUN pip install --no-cache-dir fastapi uvicorn httpx
COPY main.py .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
