import os
import httpx
import sqlite3
import json
import asyncio
import logging
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.responses import StreamingResponse

# --- Configurações ---
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434")
MASTER_API_KEY = os.getenv("MASTER_API_KEY")
# Modelos que DEVEM ficar na memória (Leves)
ALWAYS_ON_MODELS = [m.strip() for m in os.getenv("ALWAYS_ON_MODELS", "").split(",") if m.strip()]
DB_PATH = "/app/data/guard.db"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("AI_GUARD")

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# --- Banco de Dados ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS api_keys
                 (key TEXT PRIMARY KEY, name TEXT, email TEXT, created_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS usage_logs
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT, model TEXT, 
                  prompt_tokens INTEGER, eval_tokens INTEGER, timestamp TEXT)''')
    # Nova tabela para catálogo de modelos
    c.execute('''CREATE TABLE IF NOT EXISTS model_catalog
                 (name TEXT PRIMARY KEY, size INTEGER, type TEXT, last_seen TEXT)''')
    conn.commit()
    conn.close()

# --- Gerenciamento de Modelos ---
async def refresh_model_catalog():
    """Consulta o Ollama e atualiza o DB com o que temos disponível"""
    async with httpx.AsyncClient(base_url=OLLAMA_URL) as client:
        try:
            # Endpoint /api/tags lista os modelos (equivalente ao ollama list)
            resp = await client.get("/api/tags")
            if resp.status_code != 200:
                logger.error("Falha ao listar modelos do Ollama")
                return
            
            data = resp.json()
            models = data.get("models", [])
            
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            
            # Limpa catálogo antigo para garantir consistência
            c.execute("DELETE FROM model_catalog")
            
            available_models = []
            for m in models:
                name = m["name"] # ex: qwen2.5-coder:32b
                size = m.get("size", 0)
                
                # Classificação
                model_type = "always_on" if name in ALWAYS_ON_MODELS else "on_demand"
                
                c.execute("INSERT INTO model_catalog (name, size, type, last_seen) VALUES (?, ?, ?, ?)",
                          (name, size, model_type, datetime.now().isoformat()))
                available_models.append(name)
                
            conn.commit()
            conn.close()
            logger.info(f"Catálogo de modelos atualizado: {len(available_models)} modelos encontrados.")
            return available_models
            
        except Exception as e:
            logger.error(f"Erro ao atualizar catálogo: {e}")
            return []

async def ensure_always_on_models():
    """Garante que os modelos leves estejam carregados e travados na memória"""
    async with httpx.AsyncClient(base_url=OLLAMA_URL, timeout=120.0) as client:
        for model in ALWAYS_ON_MODELS:
            logger.info(f"Carregando Always-On: {model}")
            try:
                # keep_alive: -1 mantém indefinidamente
                await client.post("/api/generate", json={"model": model, "keep_alive": -1})
            except Exception as e:
                logger.error(f"Erro ao carregar {model}: {e}")

async def manage_heavy_load(target_model: str):
    """
    Lógica crítica: Se vamos carregar um modelo PESADO (não always_on),
    precisamos desligar qualquer OUTRO modelo pesado para não estourar a RAM.
    """
    if target_model in ALWAYS_ON_MODELS:
        return # Modelos leves não precisam desligar ninguém

    async with httpx.AsyncClient(base_url=OLLAMA_URL) as client:
        # 1. Verificar o que está rodando agora (Ollama ps)
        try:
            ps_resp = await client.get("/api/ps")
            running_models = ps_resp.json().get("models", [])
            
            for m in running_models:
                m_name = m["name"]
                # Se acharmos um modelo rodando que:
                # 1. NÃO é o que queremos carregar agora
                # 2. NÃO é um dos modelos leves protegidos
                if m_name != target_model and m_name not in ALWAYS_ON_MODELS:
                    logger.warning(f"Memória cheia? Descarregando modelo pesado anterior: {m_name}")
                    # keep_alive: 0 descarrega imediatamente
                    await client.post("/api/generate", json={"model": m_name, "keep_alive": 0})
                    
        except Exception as e:
            logger.error(f"Erro na gestão de memória: {e}")

# --- Ciclo de Vida ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    
    # 1. Descobre modelos e salva no SQLite
    await refresh_model_catalog()
    
    # 2. Carrega os leves
    await ensure_always_on_models()
    
    yield

app = FastAPI(lifespan=lifespan)
security = HTTPBearer()

# --- Autenticação (Mantida do anterior) ---
async def verify_credentials(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    if token == MASTER_API_KEY:
        return {"type": "master", "key": token}
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT name, email FROM api_keys WHERE key = ?", (token,))
    user = cursor.fetchone()
    conn.close()
    
    if user:
        return {"type": "user", "key": token, "name": user[0], "email": user[1]}
    
    raise HTTPException(status_code=401, detail="Invalid API Key")

# --- Endpoints ---

@app.get("/api/available_models")
def list_models(auth: dict = Depends(verify_credentials)):
    """Retorna lista de modelos categorizados do banco de dados"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row # Para retornar dicts
    c = conn.cursor()
    c.execute("SELECT name, size, type FROM model_catalog")
    rows = c.fetchall()
    conn.close()
    
    return {
        "models": [dict(row) for row in rows],
        "info": "Modelos 'always_on' estão sempre na RAM. 'on_demand' podem demorar para carregar."
    }

@app.post("/preload")
async def preload_model(request: Request, auth: dict = Depends(verify_credentials)):
    """Prepara o terreno para um modelo pesado"""
    body = await request.json()
    model_name = body.get("model")
    
    if not model_name:
        raise HTTPException(status_code=400, detail="Nome do modelo obrigatório")
    
    # Executa a limpeza de memória antes de carregar
    await manage_heavy_load(model_name)
        
    client = httpx.AsyncClient(base_url=OLLAMA_URL)
    try:
        # Carrega o novo modelo (keep_alive padrão 5m ou defina um tempo)
        # Se for um heavy model, setamos um keep_alive razoável para a sessão de uso
        await client.post("/api/generate", json={"model": model_name, "keep_alive": "10m"})
    except Exception as e:
        return {"status": "error", "detail": str(e)}
    finally:
        await client.aclose()
        
    return {"status": "ready", "model": model_name, "message": "Modelo carregado e memória limpa."}

@app.post("/admin/create_key")
async def create_key(request: Request, auth: dict = Depends(verify_credentials)):
    # Verifica se quem está pedindo é o ADMIN (Chave Mestra)
    if auth["type"] != "master":
        raise HTTPException(status_code=403, detail="Apenas a chave Mestra pode criar novas chaves.")
    
    try:
        body = await request.json()
    except:
        raise HTTPException(status_code=400, detail="JSON inválido")

    # Campos Obrigatórios (apenas para registro/organização)
    name = body.get("name")
    email = body.get("email")
    
    if not name or not email:
        raise HTTPException(status_code=400, detail="Campos 'name' e 'email' são obrigatórios para organização.")
    
    # Gera uma chave única aleatória
    raw_token = secrets.token_urlsafe(48)
    new_key = f"pbpm-{raw_token}"
    
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        # Insere no banco. Como não há UNIQUE no email, ele aceita repetições livremente.
        c.execute("INSERT INTO api_keys (key, name, email, created_at) VALUES (?, ?, ?, ?)",
                  (new_key, name, email, datetime.now().isoformat()))
        conn.commit()
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao salvar: {str(e)}")
    
    return {
        "message": "Chave criada com sucesso", 
        "api_key": new_key, 
        "registered_to": {"name": name, "email": email}
    } 

@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def gateway(path: str, request: Request, auth: dict = Depends(verify_credentials)):
    # Bloqueio de mestre para inferência
    if auth["type"] == "master" and (path.startswith("api/generate") or path.startswith("api/chat")):
         raise HTTPException(status_code=403, detail="Master Key not allowed for inference.")

    # Intercepta para gestão de memória automática
    if request.method == "POST":
        body_bytes = await request.body()
        try:
            body_json = json.loads(body_bytes)
            requested_model = body_json.get("model")
            
            # Se o usuário pediu um modelo e NÃO fez preload antes,
            # nós fazemos a gestão de memória aqui agora.
            # Vai demorar mais (tempo de unload + load), mas funciona.
            if requested_model:
                await manage_heavy_load(requested_model)
                
        except:
            pass
        
        # Reconstrói a requisição para o httpx enviar
        # (precisamos fazer isso pq lemos o stream do body acima)
        async def body_stream():
            yield body_bytes
            
        client_req_content = body_stream()
    else:
        client_req_content = None

    # ... (restante do código de proxy e log de tokens igual ao anterior) ...
    # Apenas certifique-se de passar 'client_req_content' ou 'body_bytes' corretamente no client.build_request
    
    client = httpx.AsyncClient(base_url=OLLAMA_URL)
    url = httpx.URL(path=path, query=request.url.query.encode("utf-8"))
    
    req = client.build_request(
        request.method,
        url,
        headers=request.headers.raw,
        content=body_bytes if request.method == "POST" else None,
        timeout=300.0
    )
    
    try:
        r = await client.send(req, stream=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ollama error: {e}")

    async def stream_processor():
        # ... (mesma lógica de logs de tokens) ...
        async for chunk in r.aiter_raw():
            yield chunk
            # Lógica de contagem de tokens aqui (igual anterior)

    return StreamingResponse(
        stream_processor(),
        status_code=r.status_code,
        headers=r.headers
    )