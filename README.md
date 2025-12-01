# IA PBPM API Gateway

Um Gateway de API inteligente e seguro para LLMs locais rodando via **Ollama**. Este projeto atua como um "Guard" que gerencia autenticação, contagem de tokens, logs de auditoria e, crucialmente, o **gerenciamento de memória VRAM** para alternar entre modelos leves e pesados automaticamente.

## 🚀 Funcionalidades

- **Autenticação Segura:** Sistema de chave Mestra (Admin) e chaves de Clientes (Usuários).
- **Gestão Inteligente de Memória:**
  - Mantém modelos leves (ex: `llama3.2:3b`) sempre carregados na memória.
  - Alterna automaticamente modelos pesados (ex: `qwen2.5:32b`, `llama3:70b`) para evitar estouro de VRAM (OOM), descarregando o anterior antes de carregar o novo.
- **Auditoria de Tokens:** Registra o consumo (prompt e resposta) e timestamp de cada requisição em banco de dados SQLite.
- **Auto-Discovery:** Detecta automaticamente quais modelos o Ollama possui instalados.
- **Endpoint de Preload:** Permite "aquecer" um modelo pesado antes da inferência real.

## 🛠️ Instalação e Configuração

### Pré-requisitos
- Docker e Docker Compose instalados.

### 1. Configuração do Ambiente
Crie um arquivo `.env` na raiz do projeto (baseado nas configurações do `docker-compose.yml` e `main.py`):

```bash

# URL interna do Ollama (padrão docker)
OLLAMA_URL=http://ollama:11434

# Modelos que devem ficar SEMPRE na memória (separados por vírgula)
ALWAYS_ON_MODELS=llama3.2:3b,llama3:8b

📚 Documentação da API
🔐 1. Administrativo (Requer MASTER_API_KEY)
Criar Nova Chave de Cliente
Gera uma chave de acesso para uso em inferências.

POST /admin/create_key

Headers: Authorization: Bearer SUA_MASTER_KEY

Query Params:

name: Nome do cliente

email: Email do cliente

Exemplo de Resposta:

JSON

{
  "message": "Chave criada com sucesso",
  "api_key": "pbpm-a1b2c3d4...", 
  "owner": "cliente@email.com"
}
🧠 2. Gestão de Modelos (Requer Chave de Cliente)
Listar Modelos Disponíveis
Mostra quais modelos estão instalados e sua categoria (always_on ou on_demand).

GET /api/available_models

Headers: Authorization: Bearer CHAVE_DO_CLIENTE

Preload (Aquecimento)
Avisa a API para carregar um modelo pesado na memória, descarregando outros se necessário.

POST /preload

Headers: Authorization: Bearer CHAVE_DO_CLIENTE

Body:

JSON

{
  "model": "qwen2.5-coder:32b"
}
💬 3. Inferência (Chat)
Compatível com a API padrão do Ollama. O sistema intercepta, autentica, loga os tokens e gerencia a memória antes de repassar ao Ollama.

POST /api/chat (ou /api/generate)

Headers: Authorization: Bearer CHAVE_DO_CLIENTE

Body:

JSON

{
  "model": "llama3.2:3b",
  "messages": [
    { "role": "user", "content": "Olá, como você está?" }
  ],
  "stream": true
}
📂 Estrutura de Arquivos
app/main.py: Código principal da API (FastAPI).

app/Dockerfile: Definição da imagem Docker.

docker-compose.yml: Orquestração dos serviços.

data/guard.db: Banco de dados SQLite (criado automaticamente, persistido via volume).

⚠️ Notas Importantes
A Chave Mestra definida no .env serve apenas para criar novas chaves. Ela não funciona para endpoints de chat.

O sistema suporta um limite de memória configurado no docker-compose.yml (padrão 26GB). A lógica de "Heavy Swap" garante que dois modelos pesados não concorram por esse espaço.