import os
import httpx
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

app = FastAPI()
security = HTTPBearer()

# Configs do Ambiente
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434")
VALID_API_KEY = os.getenv("API_KEY")

async def verify_api_key(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if not VALID_API_KEY:
        # Se você esquecer de configurar a senha, ele bloqueia tudo por segurança
        raise HTTPException(status_code=500, detail="Server misconfiguration: No API Key set")
    
    if credentials.credentials != VALID_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API Key")
    return credentials.credentials

@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def gateway(path: str, request: Request, token: str = Depends(verify_api_key)):
    client = httpx.AsyncClient(base_url=OLLAMA_URL)
    
    # Repassa a URL e Query params exatos
    url = httpx.URL(path=path, query=request.url.query.encode("utf-8"))
    
    try:
        # Lê o corpo da requisição do cliente
        body = await request.body()

        # Monta a requisição para o Ollama
        req = client.build_request(
            request.method,
            url,
            headers=request.headers.raw,
            content=body,
            timeout=300.0  # 5 minutos de timeout (Modelos grandes demoram)
        )

        # Envia e faz stream da resposta de volta
        r = await client.send(req, stream=True)

        return StreamingResponse(
            r.aiter_raw(),
            status_code=r.status_code,
            headers=r.headers,
            background=BackgroundTask(r.aclose)
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ollama connection error: {str(e)}")

@app.get("/health")
def health_check():
    # Endpoint público (sem senha) para saber se o container subiu
    return {"status": "online", "domain": "ia.pbpmdev.com"}
