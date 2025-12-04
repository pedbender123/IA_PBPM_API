import asyncio
import httpx
import time
import json
import statistics

# --- CONFIGURAÇÕES ---
BASE_URL = "https://ia.pbpmdev.com"
TEST_KEY = "pbpm-NRSj1_HaP7wGo0w1znL_Y2tSY8heFD5v8VlGtYkTwRrlegxG_2iTI3kuhL8FC45-"

# Modelos que vamos estressar
# O script testará um por vez para você ver a diferença de performance
MODELS_TO_TEST = ["llama3.2:3b", "llama3:8b"]

# Parâmetros de Carga (Ajuste conforme necessário)
CONCURRENT_USERS = 8      # Tentar 1 usuário por núcleo do seu EPYC é um bom teste de limite
REQUESTS_PER_MODEL = 30   # Total de requisições por modelo
PROMPT_TESTE = "Explique de forma resumida o conceito de threads em processadores."

async def run_single_request(client: httpx.AsyncClient, model: str, req_id: int):
    """Envia uma requisição de chat e mede métricas de latência e throughput."""
    url = "/api/chat"
    headers = {"Authorization": f"Bearer {TEST_KEY}"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT_TESTE}],
        "stream": True  # Obrigatório para medir TTFT e TPS real
    }

    start_time = time.time()
    token_count = 0
    first_token_time = None
    
    try:
        # Timeout alto para garantir que modelos pesados não falhem por demora na fila
        async with client.stream("POST", url, json=payload, headers=headers, timeout=120.0) as response:
            if response.status_code != 200:
                # Se der erro, lemos o corpo para saber o motivo (ex: erro de chave ou 500)
                err_text = await response.aread()
                print(f"⚠️ Req {req_id} falhou: {response.status_code} - {err_text.decode('utf-8')}")
                return None

            async for line in response.aiter_lines():
                if not line: continue
                
                # TTFT: Momento que chega o primeiro pedaço de texto
                if first_token_time is None:
                    first_token_time = time.time()

                try:
                    data = json.loads(line)
                    
                    # Contagem aproximada de tokens via stream
                    if 'message' in data and 'content' in data['message']:
                        token_count += 1
                    
                    # Métricas precisas fornecidas pelo Ollama no final do stream
                    if data.get("done") is True:
                        precise_tokens = data.get("eval_count", token_count)
                        duration_ns = data.get("eval_duration", 0)
                        
                        # Calcula inferência pura (excluindo tempo de carregar modelo na RAM)
                        inference_sec = duration_ns / 1e9 if duration_ns > 0 else (time.time() - start_time)
                        
                        return {
                            "tokens": precise_tokens,
                            "total_time": time.time() - start_time,
                            "inference_time": inference_sec,
                            "ttft": (first_token_time - start_time) if first_token_time else 0
                        }
                except:
                    pass
    except Exception as e:
        print(f"❌ Erro de conexão na Req {req_id}: {e}")
        return None

async def benchmark_model(client: httpx.AsyncClient, model: str):
    print(f"\n" + "="*60)
    print(f"🚀 INICIANDO TESTE: {model}")
    print(f"   🔥 Carga: {CONCURRENT_USERS} usuários simultâneos (Threads)")
    print(f"   🎯 Total: {REQUESTS_PER_MODEL} requisições planejadas")
    print("="*60)
    
    tasks = []
    results = []
    
    # Semáforo para garantir que não passamos do número de usuários simultâneos definidos
    sem = asyncio.Semaphore(CONCURRENT_USERS)

    async def worker(i):
        async with sem:
            # Pequeno delay para simular usuários chegando de forma natural, não todos no ms 0
            await asyncio.sleep(i * 0.05)
            print(f"   [Req {i+1:02d}] Enviando...", end="\r")
            res = await run_single_request(client, model, i)
            if res:
                results.append(res)
                tps = res['tokens'] / res['inference_time'] if res['inference_time'] > 0 else 0
                print(f"   [Req {i+1:02d}] ✅ Concluída: {res['tokens']} tokens | Vel: {tps:.1f} t/s")

    # Dispara todas as tarefas
    start_bench = time.time()
    await asyncio.gather(*(worker(i) for i in range(REQUESTS_PER_MODEL)))
    total_bench_time = time.time() - start_bench

    # --- RELATÓRIO ---
    if not results:
        print(f"\n❌ Falha total no teste do modelo {model}. Verifique a chave e a API.")
        return

    # Cálculos estatísticos
    tps_list = [r['tokens'] / r['inference_time'] for r in results if r['inference_time'] > 0]
    ttft_list = [r['ttft'] for r in results]
    total_tokens = sum(r['tokens'] for r in results)
    
    avg_tps = statistics.mean(tps_list) if tps_list else 0
    max_tps = max(tps_list) if tps_list else 0
    min_tps = min(tps_list) if tps_list else 0
    avg_ttft = statistics.mean(ttft_list) if ttft_list else 0
    
    # Throughput real do servidor (Tokens Totais / Tempo Total do Teste)
    server_throughput = total_tokens / total_bench_time

    print(f"\n📊 RESULTADOS FINAIS: {model}")
    print(f"   ✅ Sucesso: {len(results)}/{REQUESTS_PER_MODEL}")
    print(f"   ⏱️ Tempo Total: {total_bench_time:.2f}s")
    print("-" * 30)
    print(f"   ⚡ TPS Médio (por usuário):   {avg_tps:.2f} t/s")
    print(f"   🚀 Throughput Servidor (Total): {server_throughput:.2f} t/s  <-- CAPACIDADE DA VPS")
    print(f"   🐢 Pior TPS registrado:       {min_tps:.2f} t/s")
    print(f"   🐇 Melhor TPS registrado:     {max_tps:.2f} t/s")
    print(f"   ⏳ Latência Média (TTFT):     {avg_ttft:.3f}s")
    print("="*60 + "\n")

async def main():
    # Configuração do cliente HTTP
    limits = httpx.Limits(max_keepalive_connections=20, max_connections=30)
    async with httpx.AsyncClient(base_url=BASE_URL, limits=limits, timeout=60.0) as client:
        
        # 1. Health Check rápido
        print(f"📡 Verificando conexão com {BASE_URL}...")
        try:
            # Tenta listar modelos para validar a chave fornecida
            resp = await client.get("/api/available_models", headers={"Authorization": f"Bearer {TEST_KEY}"})
            if resp.status_code == 200:
                print(f"✅ Conexão OK! Modelos disponíveis: {len(resp.json().get('models', []))}")
            elif resp.status_code == 401 or resp.status_code == 403:
                print(f"❌ Erro de Autenticação: A chave '{TEST_KEY[:10]}...' foi recusada.")
                return
            else:
                print(f"⚠️ Aviso: API respondeu com status {resp.status_code}")
        except Exception as e:
            print(f"❌ Não foi possível conectar: {e}")
            return

        # 2. Executa Benchmark para cada modelo
        for model in MODELS_TO_TEST:
            await benchmark_model(client, model)
            print("❄️  Resfriando por 5 segundos...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Teste interrompido pelo usuário.")