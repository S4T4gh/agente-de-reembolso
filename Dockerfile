# Imagem do agente de reembolso (porta 8000).
# Build: docker build -t agente .
# Run:   docker run --env-file <env> -p 8000:8000 agente
# O indice em storage/ e copiado pronto; nao e reconstruido no build/start.

FROM python:3.11-slim

# Dependencias de sistema: OCR (poppler/tesseract) e compilacao do BM25.
RUN apt-get update && apt-get install -y --no-install-recommends \
        poppler-utils tesseract-ocr tesseract-ocr-por \
        gcc python3-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && apt-get purge -y gcc python3-dev \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Artefatos de runtime: indice hibrido, base normativa e codigo.
COPY storage/ ./storage/
COPY kb/ ./kb/
COPY app/ ./app/
COPY casos_treino/ ./casos_treino/
COPY anexos/treino/ ./anexos/treino/
# Pacote da operadora. So e iniciado quando RENDER=true (ver docker/iniciar.sh).
COPY mcp/mcp_operadora/ ./mcp_operadora/
COPY docker/iniciar.sh /srv/iniciar.sh
RUN chmod +x /srv/iniciar.sh

# Variaveis de ambiente injetadas no run:
# BOOTCAMP_LLM_ENDPOINT, BOOTCAMP_API_KEY, MCP_OPERADORA_URL, MCP_OPERADORA_TOKEN
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=40s --retries=6 \
    CMD python -c "import os,urllib.request; p=os.environ.get('PORT') or '8000'; urllib.request.urlopen('http://127.0.0.1:%s/health'%p)"

CMD ["sh", "/srv/iniciar.sh"]
