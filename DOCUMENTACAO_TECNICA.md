# Documentação Técnica — Agente de Reembolso (SaúdeMais)

**Projeto:** agente conversacional multiagente para análise automatizada de pedidos de reembolso em saúde suplementar  
**Origem:** Desafio técnico Bootcamp 2026 (prova 339EF4)  
**Stack principal:** Python 3.11 · FastAPI · LangGraph · LlamaIndex · Pydantic · MCP · Docker  

---

## 1. Visão geral

O sistema simula o atendimento de uma operadora de saúde: o beneficiário conversa por chat, envia recibos/notas (PDF ou imagem) e recebe uma decisão fundamentada — ou o encaminhamento para analista humano quando o caso está fora da alçada automatizada.

O problema central **não** é apenas “chamar um LLM”. É orquestrar, em vários turnos:

1. identidade e elegibilidade (cadastro);
2. classificação e extração do documento fiscal;
3. recuperação da norma **vigente** em um corpus com circulares que se sobrepõem;
4. cálculo determinístico do valor;
5. guardrails de privacidade e escopo (LGPD / art. 7–8 do regulamento fictício).

Cada tipo de informação tem **fonte obrigatória** (misturar fontes é erro crítico):

| Informação | Fonte |
|---|---|
| Carteirinha | beneficiário |
| Plano, adesão, status, histórico, sessões | MCP da operadora |
| Categoria, valor, data, TUSS | documento anexado |
| Regra vigente e fundamentação | base normativa (`kb/`) via RAG |

---

## 2. Arquitetura

### 2.1 Visão em camadas

```
???????????????????????????????????????????????????????????????
?  Cliente HTTP (avaliador / front / TCC)                     ?
?  POST /chat · GET /health · POST /reset                     ?
???????????????????????????????????????????????????????????????
                            ?
???????????????????????????????????????????????????????????????
?  API FastAPI (app/main.py)                                  ?
?  Contrato Pydantic ChatRequest / ChatResponse               ?
???????????????????????????????????????????????????????????????
                            ?
???????????????????????????????????????????????????????????????
?  Supervisor LangGraph (app/agents/supervisor/graph.py)      ?
?  Estado por session_id (MemorySaver)                        ?
?         ????????????????????????????????????????????        ?
?         ?             ?              ?             ?        ?
?    Triagem       Documento        Normas           ?        ?
?  identidade     OCR/classif.    RAG + decisão      ?        ?
?  art. 8         verificação     cálculo            ?        ?
??????????????????????????????????????????????????????        ?
          ?             ?              ?                      ?
          ?             ?              ?                      ?
     MCP tools     Extrator PDF    LlamaIndex híbrido         ?
     (cadastro)    / imagem        BM25 + denso + RRF         ?
                   Verificação     Cálculo determinístico     ?
                   (pré-cálculo)                              ?
???????????????????????????????????????????????????????????????
```

### 2.2 Por que supervisor + subagentes

Um monolito `if/elif` não separa responsabilidades nem permite handoff explícito. O grafo LangGraph:

- roteia por intenção (anexo, carteirinha, dúvida normativa, terceiro);
- mantém **estado acumulado** entre turnos (o avaliador **não** reenvia o histórico);
- isola falhas (ex.: MCP indisponível não derruba o processo inteiro com 500).

Nós do grafo:

| Nó | Responsabilidade |
|---|---|
| `supervisor` | Decide o próximo subagente |
| `triagem` | Carteirinha, art. 8 (terceiros), acolhimento |
| `documento` | Extração, classificação, **verificação** (sem aprovar valor) |
| `normas` | Dúvidas com RAG; **só calcula** após veredito OK |

Ordem crítica (lição da devolutiva da banca):

```
documento  ?  verificação escreve veredito
                    ?
         ???????????????????????
         ?          ?          ?
    INCOMPLETO  BLOQUEADO     OK
    (não aprova) (NEGADO /   ? normas calcula valor
                  PENDENTE /
                  ESCALADO)
```

Estado incompleto **nunca** vira aprovação com datas/adesão inventadas.

---

## 3. Contrato HTTP

Porta **8000**.

| Método | Rota | Função |
|---|---|---|
| `GET` | `/health` | Liveness (200 em até 60 s após o start) |
| `POST` | `/chat` | Um turno da conversa |
| `POST` | `/reset` | Limpa sessões / checkpointer |

### Requisição (`ChatRequest`)

```json
{
  "session_id": "sess-001",
  "mensagem": "fui no psicólogo, dá pra pedir reembolso?",
  "anexo": {
    "filename": "recibo.pdf",
    "mime_type": "application/pdf",
    "base64": "..."
  }
}
```

O anexo é opcional e pode chegar em **qualquer** turno (inclusive antes da carteirinha).

### Resposta (`ChatResponse`)

Campos de decisão ficam `null` até a conversa chegar lá.

| Campo | Descrição |
|---|---|
| `resposta` | Texto ao beneficiário |
| `categoria_documento` | Uma das 7 classes |
| `decisao` | Uma das 6 decisões |
| `valor_solicitado_brl` | Valor do documento |
| `valor_reembolso_brl` | Valor calculado (null em OPME/escalado) |
| `regras_aplicadas` | Dispositivos citados (ex.: `ART-35`, `CIRC-02-2026`) |
| `protocolo` | Protocolo MCP quando escalado |
| `pendencias` | Documentos/dados faltantes |

**Categorias:** `CONSULTA_MEDICA`, `SESSAO_TERAPIA`, `EXAME_DIAGNOSTICO`, `RELATORIO_CLINICO`, `MATERIAL_OPME`, `DESPESA_NAO_COBERTA`, `INVALIDO`.

**Decisões:** `APROVADO`, `APROVADO_PARCIAL`, `PENDENTE_DOCUMENTO`, `NEGADO`, `FORA_DE_ESCOPO`, `ESCALADO_ANALISTA`.

---

## 4. Estado de sessão

Definido em `app/agents/state.py` (`AgentState`). Persiste via `MemorySaver` indexado por `session_id` (`thread_id` do LangGraph).

Campos principais:

- identidade: `carteirinha_sessao`, `beneficiario`, `historico`;
- documento: `documento`, `categoria_documento`, `anexo_pendente`, `tem_relatorio`;
- apuração: `verificacao` (`OK` \| `BLOQUEADO` \| `INCOMPLETO`), `pronto_para_calculo`;
- desfecho: `decisao`, valores, `regras_aplicadas`, `protocolo`, `pendencias`;
- saída: `resposta`, `handoff`.

O anexo pode ser guardado em `anexo_pendente` até a carteirinha chegar; no checkpoint, limpeza usa `""` / `{}` porque `None` **não sobrescreve** estado no LangGraph.

---

## 5. Subagentes

### 5.1 Triagem (`app/agents/triagem/node.py`)

- Extrai carteirinha (16 dígitos) e consulta MCP (`consultar_beneficiario`, `consultar_historico`).
- Recusa pedidos sobre terceiros (cônjuge/dependente / outra carteirinha) — art. 8.
- Se já houver desfecho do titular, **não** sobrescreve com `FORA_DE_ESCOPO` (preserva o pedido em curso).
- Dúvidas normativas são encaminhadas a `normas` em vez de só pedir anexo.

### 5.2 Documento (`app/agents/documento/`)

- Extração OCR/PDF (`extrator.py`: PyMuPDF + Tesseract).
- Classificação nas 7 categorias.
- Chamada a `verificar_elegibilidade` **antes** de qualquer aprovação.
- Bloqueios: carência, prazo, despesa não coberta, OPME/alçada, relatório obrigatório, estado incompleto.
- OPME / alçada: abre protocolo MCP e devolve `ESCALADO_ANALISTA` **sem** informar valor de reembolso.

### 5.3 Normas (`app/agents/normas/node.py`)

- Responde a pergunta do turno (templates + RAG + LLM).
- Só calcula valor se `verificacao == OK` (re-verifica e **contesta** valor prévio se o veredito mudou).
- Fundamenta com dispositivos da base.

---

## 6. Cálculo e verificação

| Módulo | Papel |
|---|---|
| `app/calculo/verificacao.py` | Veredito de elegibilidade |
| `app/calculo/reembolso.py` | Cálculo determinístico (teto URS, coparticipação, limite anual) |

Regras típicas (corpus fictício da prova):

- teto de consulta / terapia em URS;
- coparticipação por plano e tempo de adesão (art. 44);
- limite anual 48 URS (art. 45/47);
- vigência de circulares (ex.: Circular 02/2026 altera tetos e exigência de relatório na 5ª sessão).

O LLM **não** inventa o valor: o motor numérico é determinístico; o modelo ajuda na linguagem e na recuperação normativa.

---

## 7. RAG (LlamaIndex)

Indexação **fora** do container:

```bash
python -m ingest.build
```

Artefatos em `storage/` (copiados no `Dockerfile`, sem rebuild em runtime):

- `storage/vector/` — SimpleVectorStore (denso);
- `storage/bm25_nodes.pkl` — corpus lexical;
- `storage/tabela_urs.json` — lookup de procedimentos;
- `storage/meta.json` — metadados do índice.

Recuperação (`app/rag/retriever.py`):

1. BM25 (top-k);
2. busca densa (top-k);
3. fusão **RRF** (Reciprocal Rank Fusion);
4. rerank leve;
5. contexto limitado (teto de tokens de entrada do gateway).

Corpus em `kb/`: regulamento, circulares, anexo de exclusões, nota técnica, FAQ, tabela URS, etc. Há **revogação em cadeia** e FAQ desatualizado — a recuperação precisa privilegiar a norma vigente.

---

## 8. Integração MCP

Cliente: `app/tools/mcp_client.py`.

URL e token **somente** por ambiente:

- `MCP_OPERADORA_URL`
- `MCP_OPERADORA_TOKEN`

Ferramentas:

| Tool | Uso |
|---|---|
| `consultar_beneficiario` | Plano, adesão, status |
| `consultar_historico` | Pedidos anteriores / sessões / valores já pagos |
| `abrir_protocolo` | Escalonamento para analista |

O servidor de treino está em `mcp/` (`docker compose up mcp`).

---

## 9. Guardrails

`app/guardrails/privacidade.py` (e sanitização na saída):

- não ecoar CPF completo (mascarar);
- não ecoar CID / hipótese diagnóstica;
- não responder sobre carteirinha diferente da sessão;
- pedido de terceiro ? recusa explícita (+ `FORA_DE_ESCOPO` se ainda não houver desfecho do titular).

Falhas internas em `/chat` devolvem resposta segura com pendência `falha_interna_temporaria` (sem HTTP 500).

---

## 10. Modelo de linguagem

Configuração centralizada em `app/llm.py` (gateway do bootcamp / Gemini 2.5 Flash Lite).

Variáveis:

- `BOOTCAMP_LLM_ENDPOINT`
- `BOOTCAMP_API_KEY`

Uso típico:

- embeddings do RAG;
- resposta fluente a dúvidas em `normas` (ancorada nos trechos recuperados);
- **não** substitui o motor de cálculo.

---

## 11. Deploy

### Build / run

```bash
docker build -t reembolso .
docker run -d --env-file .env -p 8000:8000 reembolso
```

A imagem copia apenas `app/`, `kb/` e `storage/`. Índice pré-construído; build offline após o PyPI; alvo &lt; 4 GB e &lt; 10 min.

### Compose local (MCP + agente)

```bash
docker compose up -d
```

### Validação de treino

```bash
python rodar_treino.py --roteiro-fixo   # roteiro fixo
python rodar_treino.py -v                # beneficiário simulado (redação variável)
```

---

## 12. Estrutura de pastas

```
app/
  main.py                 # FastAPI
  schemas.py              # Contrato Pydantic
  llm.py                  # Gateway / embeddings
  agents/
    supervisor/graph.py   # Orquestração LangGraph
    triagem/              # Identidade e escopo
    documento/            # Extração + verificação
    normas/               # RAG + decisão/cálculo
    state.py
  calculo/
    verificacao.py
    reembolso.py
  rag/retriever.py
  tools/mcp_client.py
  guardrails/
kb/                       # Base normativa
storage/                  # Índice commitado
ingest/build.py           # Pipeline de indexação
mcp/                      # Servidor MCP de treino
casos_treino/             # 3 conversas de validação
avaliacao/                # Motor de correção
Dockerfile
requirements.txt
```

---

## 13. Fluxo ponta a ponta (exemplo)

1. Beneficiário manda só o PDF ? triagem guarda anexo e pede carteirinha.  
2. Envia 16 dígitos ? MCP carrega cadastro/histórico ? documento classifica e **verifica**.  
3. Se OK ? normas calcula (teto + copart + limite anual) e responde com decisão + regras.  
4. Se OPME / alçada ? protocolo MCP + `ESCALADO_ANALISTA` sem valor.  
5. Se perguntar “por que não volta o valor integral?” ? normas responde com RAG/norma, sem reiniciar o fluxo.  
6. Se falar da esposa ? triagem recusa (art. 8) e mantém o pedido do titular.

---

## 14. Limitações conhecidas e melhorias para TCC

**Limitações atuais**

- Corpus e regulamento são **fictícios** (prova); não usar como regra real de operadora.
- Checkpointer em memória (não distribuído).
- Extração documental baseada em heurísticas + OCR (sensível a qualidade da foto).
- Dependência de gateway LLM externo para linguagem/embeddings.

**Direções de pesquisa / TCC**

1. **Multiagente + RAG jurídico/regulatório** — vigência, revogação e citação fiel de dispositivos.  
2. **Separação verificação × cálculo × linguagem** — reduzir alucinação em decisões financeiras.  
3. **Avaliação conversacional turno a turno** — métricas além de accuracy de classificação.  
4. **Privacidade em agentes de saúde** — guardrails e evidências de não vazamento.  
5. **Human-in-the-loop** — escalonamento (OPME/alçada) como padrão de responsabilidade.

---

## 15. Como citar o projeto (sugestão)

> Sistema multiagente para análise conversacional de reembolso em saúde suplementar, orquestrado com LangGraph, recuperação híbrida (BM25 + embeddings + RRF) sobre base normativa versionada, integração MCP para dados cadastrais e cálculo determinístico de valores, exposto via API containerizada.

---

*Documento gerado a partir do código do repositório `reembolso-bootcamp-2026`. Atualizar este arquivo quando a arquitetura mudar.*
