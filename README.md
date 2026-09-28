# Agente de reembolso

Agente conversacional que analisa pedidos de reembolso em saude suplementar. O beneficiario conversa, envia o recibo e recebe uma decisao fundamentada, ou o encaminhamento para um analista humano quando o caso esta fora da alcada automatizada.

O sistema nao e um unico prompt. Ele separa identidade, documento, norma e calculo, e so aprova um valor depois de uma verificacao explicita.

## O que o agente faz

1. Identifica o beneficiario pela carteirinha e consulta o cadastro.
2. Classifica o anexo (PDF ou imagem) e extrai valor, data e codigo do procedimento.
3. Recupera a regra vigente na base normativa.
4. Verifica elegibilidade (carencia, prazo, cobertura, alcada, documentos obrigatorios).
5. Calcula o reembolso de forma deterministica, ou escala o caso sem informar valor.

Cada dado tem uma fonte propria. Misturar as fontes e o erro mais grave do fluxo.

| Informacao | Fonte |
|---|---|
| Carteirinha | O beneficiario informa |
| Plano, adesao, situacao, sessoes e historico | MCP da operadora |
| Categoria, valor pago, data e codigo TUSS | Documento anexado |
| Regra vigente e fundamentacao | Base em `kb/`, via busca hibrida |

## Arquitetura

Supervisor em LangGraph com tres subagentes e handoff explicito. O estado da conversa fica no checkpointer, indexado por `session_id`. O cliente nao reenvia o historico a cada turno.

```
Cliente HTTP
    |
    v
FastAPI  /health  /chat  /reset
    |
    v
Supervisor (LangGraph + MemorySaver)
    |
    +-- triagem     identidade, art. 8 (terceiros)
    +-- documento   OCR, classificacao, verificacao
    +-- normas      RAG, duvidas e calculo apos veredito OK
            |
            +-- MCP          cadastro, historico, protocolo
            +-- LlamaIndex   BM25 + busca densa + fusao RRF
            +-- calculo      teto, coparticipacao, limite anual
```

| No | Arquivo | Papel |
|---|---|---|
| supervisor | `app/agents/supervisor/graph.py` | Escolhe o proximo subagente |
| triagem | `app/agents/triagem/node.py` | Carteirinha, cadastro e recusa de terceiro |
| documento | `app/agents/documento/node.py` | Extracao e veredito, sem aprovar valor |
| normas | `app/agents/normas/node.py` | Responde a pergunta do turno e calcula so se a verificacao for OK |

Ordem obrigatoria da apuracao:

```
documento -> verificacao
                 |
     INCOMPLETO  |  BLOQUEADO           |  OK
     nao aprova  |  NEGADO, PENDENTE    |  normas calcula o valor
                 |  ou ESCALADO         |
```

Estado incompleto nao vira aprovacao. Datas e adesao ausentes nao sao preenchidas com valor padrao.

## Contrato HTTP

Porta **8000**.

| Metodo | Rota | Funcao |
|---|---|---|
| `GET` | `/` | Pagina do servico no ar |
| `GET` | `/health` | Confirma que o processo subiu |
| `POST` | `/chat` | Um turno da conversa |
| `POST` | `/reset` | Limpa sessoes e o checkpointer |

Pedido:

```json
{
  "session_id": "sess-001",
  "mensagem": "fui no psicologo, da pra pedir reembolso?",
  "anexo": {
    "filename": "recibo.pdf",
    "mime_type": "application/pdf",
    "base64": "..."
  }
}
```

O anexo e opcional e pode chegar em qualquer turno, inclusive antes da carteirinha.

Resposta (`ChatResponse`):

| Campo | Significado |
|---|---|
| `resposta` | Texto para o beneficiario |
| `categoria_documento` | Classe do documento, ou `null` |
| `decisao` | Decisao, ou `null` enquanto a analise nao fechou |
| `valor_solicitado_brl` | Valor lido no documento |
| `valor_reembolso_brl` | Valor calculado; `null` quando escalado |
| `regras_aplicadas` | Dispositivos citados, por exemplo `ART-35` |
| `protocolo` | Numero aberto no MCP, quando houver escalonamento |
| `pendencias` | O que ainda falta |

Categorias: `CONSULTA_MEDICA`, `SESSAO_TERAPIA`, `EXAME_DIAGNOSTICO`, `RELATORIO_CLINICO`, `MATERIAL_OPME`, `DESPESA_NAO_COBERTA`, `INVALIDO`.

Decisoes: `APROVADO`, `APROVADO_PARCIAL`, `PENDENTE_DOCUMENTO`, `NEGADO`, `FORA_DE_ESCOPO`, `ESCALADO_ANALISTA`.

## Recuperacao normativa

O indice e construido fora do container e fica em `storage/`. O Dockerfile so copia esse diretorio.

```bash
python -m ingest.build
```

A busca em `app/rag/retriever.py` combina BM25 e vetor denso, funde os rankings com RRF e reranqueia. O corpus em `kb/` inclui regulamento, circulares, exclusoes, nota tecnica, FAQ e tabela de procedimentos. Circulares alteram artigos anteriores; a recuperacao precisa da regra vigente, nao de um trecho desatualizado.

## Calculo

| Modulo | Papel |
|---|---|
| `app/calculo/verificacao.py` | Veredito: `OK`, `BLOQUEADO` ou `INCOMPLETO` |
| `app/calculo/reembolso.py` | Teto em URS, coparticipacao, limite anual |

O modelo de linguagem redige a resposta e ajuda na busca. O valor numerico sai do motor deterministico.

Material OPME e pedidos acima da alcada abrem protocolo via MCP, devolvem `ESCALADO_ANALISTA` e nao informam valor de reembolso.

## Guardrails

- CPF completo e mascarado na resposta.
- Codigo CID e hipotese diagnostica nao sao repetidos.
- Pedido sobre outra carteirinha, conjuge ou dependente e recusado.
- Se o titular ja tiver desfecho, essa recusa nao apaga a decisao dele.
- Falha interna em `/chat` devolve uma resposta segura, sem HTTP 500.

## Como executar

Requisitos: Python 3.11 e as quatro variaveis de ambiente.

```text
BOOTCAMP_LLM_ENDPOINT
BOOTCAMP_API_KEY
MCP_OPERADORA_URL
MCP_OPERADORA_TOKEN
```

Copie `.env.example` para `.env` e preencha. O `.env` nao entra no Git.

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m app.llm
```

Subir MCP de treino e o agente:

```bash
docker compose up -d
```

Ou so a imagem do agente, com o indice ja presente em `storage/`:

```bash
docker build -t agente-de-reembolso .
docker run --env-file .env -p 8000:8000 agente-de-reembolso
```

Conferir:

```bash
curl http://localhost:8000/health
```

Os tres casos de treino ficam em `casos_treino/`:

```bash
python rodar_treino.py --roteiro-fixo
python rodar_treino.py -v
```

## Estrutura

```text
app/main.py                  API
app/schemas.py               Contrato Pydantic
app/llm.py                   Modelo e embeddings
app/agents/supervisor/       Grafo e roteamento
app/agents/triagem/          Identidade e escopo
app/agents/documento/        Extracao e verificacao
app/agents/normas/           RAG, duvida e calculo
app/calculo/                 Veredito e valor
app/rag/                     Busca hibrida
app/tools/mcp_client.py      Cliente MCP
app/guardrails/              Privacidade da saida
kb/                          Documentos normativos
storage/                     Indice pronto
ingest/build.py              Construcao do indice
mcp/                         Servidor MCP de treino
casos_treino/                Conversas de validacao
Dockerfile
```

## Fluxo de exemplo

1. O beneficiario envia so o PDF. A triagem guarda o arquivo e pede a carteirinha.
2. Ele informa os 16 digitos. O MCP carrega cadastro e historico.
3. O no de documento classifica o recibo e escreve o veredito.
4. Se o veredito for OK, normas calcula teto, coparticipacao e limite anual.
5. Se for OPME ou estiver acima da alcada, abre protocolo e nao informa valor.
6. Uma pergunta no meio da conversa ("por que nao volta o valor inteiro?") vai para normas, com a base normativa, sem reiniciar o pedido.
7. Um pedido sobre outra pessoa e recusado e o atendimento do titular continua.

## Stack

Python 3.11, FastAPI, Pydantic, LangGraph, LlamaIndex, Gemini (gateway configuravel), MCP, PyMuPDF, Tesseract e Docker.

## Limites

O regulamento deste repositorio e ficticio e serve para exercitar o fluxo. O checkpointer e em memoria. A extracao do recibo depende da qualidade do PDF ou da foto. Embeddings e redacao dependem do endpoint de modelo configurado no `.env`.
