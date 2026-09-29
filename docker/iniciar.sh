#!/bin/sh
# Fora do Render o agente sobe sozinho e usa o MCP_OPERADORA_URL do ambiente.
# No Render os dois processos acordam juntos: a operadora fica em 127.0.0.1:9000
# e nao depende de um segundo servico dormindo.

set -e

if [ "$RENDER" = "true" ]; then
  export MCP_OPERADORA_HOST=127.0.0.1
  export MCP_OPERADORA_PORT=9000
  export MCP_OPERADORA_DADOS=/srv/casos_treino
  if [ -z "$MCP_OPERADORA_TOKEN" ]; then
    export MCP_OPERADORA_TOKEN=treino
  fi
  # PORT e a porta publica do agente. O MCP nao pode ocupa-la.
  env -u PORT python -m mcp_operadora.server &
  ok=0
  i=0
  while [ "$i" -lt 30 ]; do
    if python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9000/saude')"; then
      ok=1
      break
    fi
    i=$((i + 1))
    sleep 1
  done
  if [ "$ok" -ne 1 ]; then
    echo "operadora local nao subiu" >&2
    exit 1
  fi
  export MCP_OPERADORA_URL=http://127.0.0.1:9000/mcp
  exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 8000
