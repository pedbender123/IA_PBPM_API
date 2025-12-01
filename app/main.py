import os
import httpx
import sqlite3
import secrets
import json
import logging
import asyncio
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.responses import StreamingResponse

# --- Configurações ---
# Tenta pegar MASTER_API_KEY, se não achar, tenta API_KEY (retrocompatibilidade)
MASTER_API_KEY = os.getenv("MASTER_API_KEY") or os.getenv("API_KEY")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434")
ALWAYS_ON_MODELS = [m.strip() for m in os.getenv("ALWAYS_ON_MODELS", "").split(",") if m.strip()]
DB_PATH = "/app/data/guard.db"

# Configuração de Logs
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("AI_GUARD")

# Garante que a pasta de dados existe
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# --- Banco de Dados ---
def init_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS api_keys
                     (key TEXT PRIMARY KEY, name TEXT, email TEXT, created_at TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS usage_logs
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT, model TEXT, 
                      prompt_tokens INTEGER, eval_tokens INTEGER, timestamp TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS model_catalog
                     (name TEXT PRIMARY KEY, size INTEGER, type TEXT, last_seen TEXT)''')
        conn.commit()
        conn.close()
        logger.info("Banco de dados inicializado com sucesso.")
    except Exception as e:
        logger.error(f"Erro ao inicializar banco de dados: {e}")

async def log_usage(key: str, model: str, prompt_tokens: int, eval_tokens: int):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO usage_logs (key, model, prompt_tokens, eval_tokens, timestamp) VALUES (?, ?, ?, ?, ?)",
                  (key, model, prompt_tokens, eval_tokens, datetime.now().isoformat()))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Erro ao salvar log: {e}")

# --- Gerenciamento de Modelos ---
async def refresh_model_catalog():
    async with httpx.AsyncClient(base_url=OLLAMA_URL) as client:
        try:
            resp = await client.get("/api/tags")
            if resp.status_code != 200: return
            
            data = resp.json()
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("DELETE FROM model_catalog")
            for m in data.get("models", []):
                name = m["name"]
                size = m.get("size", 0)
                m_type = "always_on" if name in ALWAYS_ON_MODELS else "on_demand"
                c.execute("INSERT INTO model_catalog (name, size, type, last_seen) VALUES (?, ?, ?, ?)",
                          (name, size, m_type, datetime.now().isoformat()))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Erro catalogo: {e}")

async def ensure_always_on_models():
    async with httpx.AsyncClient(base_url=OLLAMA_URL, timeout=120.0) as client:
        for model in ALWAYS_ON_MODELS:
            try:
                await client.post("/api/generate", json={"model": model, "keep_alive": -1})
                logger.info(f"Modelo Always-On carregado: {model}")
            except Exception as e:
                logger.error(f"Falha ao carregar {model}: {e}")

async def manage_heavy_load(target_model: str):
    if target_model in ALWAYS_ON_MODELS: return
    async with httpx.AsyncClient(base_url=OLLAMA_URL) as client:
        try:
            ps_resp = await client.get("/api/ps")
            for m in ps_resp.json().get("models", []):
                if m["name"] != target_model and m["name"] not in ALWAYS_ON_MODELS:
                    logger.warning(f"Descarregando pesado: {m['name']}")
                    await client.post("/api/generate", json={"model": m["name"], "keep_alive": 0})
        except: pass

# --- Ciclo de Vida ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db() # Cria as tabelas na inicialização
    await refresh_model_catalog()
    await ensure_always_on_models()
    yield

app = FastAPI(lifespan=lifespan)
security = HTTPBearer()

# --- Autenticação ---
async def verify_credentials(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    
    # Verifica Chave Mestra
    if MASTER_API_KEY and token == MASTER_API_KEY:
        return {"type": "master", "key": token}
    
    # Verifica Banco de Dados (com try/except para evitar crash 500)
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT name, email FROM api_keys WHERE key = ?", (token,))
        user = cursor.fetchone()
        conn.close()
        if user:
            return {"type": "user", "key": token, "name": user[0], "email": user[1]}
    except Exception as e:
        logger.error(f"Erro DB Auth: {e}")
        # Se der erro no DB, nega acesso mas não crasha
        pass
    
    raise HTTPException(status_code=401, detail="Invalid API Key")

# --- Endpoints ---
@app.post("/admin/create_key")
async def create_key(request: Request, auth: dict = Depends(verify_credentials)):
    if auth["type"] != "master":
        raise HTTPException(status_code=403, detail="Acesso negado")
    
    try:
        body = await request.json()
    except:
        raise HTTPException(status_code=400, detail="JSON inválido")

    name = body.get("name")
    email = body.get("email")
    if not name or not email:
        raise HTTPException(status_code=400, detail="Nome e Email obrigatórios")
    
    new_key = f"pbpm-{secrets.token_urlsafe(48)}"
    
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO api_keys (key, name, email, created_at) VALUES (?, ?, ?, ?)",
                  (new_key, name, email, datetime.now().isoformat()))
        conn.commit()
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
    return {"message": "Criado", "api_key": new_key, "registered_to": {"name": name, "email": email}}

@app.get("/api/available_models")
def list_models(auth: dict = Depends(verify_credentials)):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT name, size, type FROM model_catalog").fetchall()
    conn.close()
    return {"models": [dict(r) for r in rows]}

@app.post("/preload")
async def preload_model(request: Request, auth: dict = Depends(verify_credentials)):
    body = await request.json()
    model = body.get("model")
    if model:
        await manage_heavy_load(model)
        async with httpx.AsyncClient(base_url=OLLAMA_URL) as client:
            await client.post("/api/generate", json={"model": model, "keep_alive": "10m"})
    return {"status": "ready", "model": model}

@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def gateway(path: str, request: Request, auth: dict = Depends(verify_credentials)):
    if auth["type"] == "master" and ("generate" in path or "chat" in path):
        raise HTTPException(status_code=403, detail="Master key não pode fazer inferência")

    client_req_content = None
    if request.method == "POST":
        body_bytes = await request.body()
        try:
            body_json = json.loads(body_bytes)
            if "model" in body_json:
                await manage_heavy_load(body_json["model"])
        except: pass
        
        async def body_stream(): yield body_bytes
        client_req_content = body_stream()

    client = httpx.AsyncClient(base_url=OLLAMA_URL)
    url = httpx.URL(path=path, query=request.url.query.encode("utf-8"))
    req = client.build_request(request.method, url, headers=request.headers.raw, content=client_req_content, timeout=300.0)
    
    try:
        r = await client.send(req, stream=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ollama error: {e}")

    async def stream_processor():
        prompt_t, eval_t = 0, 0
        async for chunk in r.aiter_raw():
            yield chunk
            try:
                txt = chunk.decode("utf-8", errors="ignore")
                if '"done":true' in txt or '"done": true' in txt:
                    for line in txt.split("\n"):
                        if '"done":' in line:
                            d = json.loads(line)
                            prompt_t = d.get("prompt_eval_count", 0)
                            eval_t = d.get("eval_count", 0)
            except: pass
        
        await r.aclose()
        await client.aclose()
        if auth["type"] == "user":
            await log_usage(auth["key"], "unknown", prompt_t, eval_t)

    return StreamingResponse(stream_processor(), status_code=r.status_code, headers=r.headers)