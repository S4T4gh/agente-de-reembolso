# Imagem da operadora de treino.
# Build a partir da raiz do repositorio:
#   docker build -f mcp/Dockerfile -t operadora .
# O Render usa o mesmo contexto (.) e este Dockerfile.

FROM python:3.11-slim
WORKDIR /srv
COPY mcp/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY mcp/mcp_operadora/ ./mcp_operadora/
COPY casos_treino/ /dados/
ENV MCP_OPERADORA_HOST=0.0.0.0 \
    MCP_OPERADORA_PORT=9000 \
    MCP_OPERADORA_DADOS=/dados \
    PYTHONUNBUFFERED=1
EXPOSE 9000
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import os,urllib.request; p=os.environ.get('PORT') or os.environ.get('MCP_OPERADORA_PORT','9000'); urllib.request.urlopen('http://127.0.0.1:%s/saude'%p)"
CMD ["python", "-m", "mcp_operadora.server"]
