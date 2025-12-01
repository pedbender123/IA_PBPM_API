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