"""Extracao e classificacao de campos a partir de anexos PDF/imagem."""

from __future__ import annotations

import base64
import io
import re
from dataclasses import dataclass, field
from decimal import Decimal

import fitz
from PIL import Image
import pytesseract

from app.schemas import Categoria

@dataclass
class DocumentoExtraido:
    texto: str
    categoria: Categoria
    valor_pago: Decimal | None = None
    data_atendimento: str | None = None
    codigo_tuss: str | None = None
    nome_beneficiario: str | None = None
    sessao_ano: int | None = None
    itens: list[dict] = field(default_factory=list)
    invalido_motivo: str | None = None

_RE_VALOR = re.compile(r"(?:R\$\s*|import[a\u00e2]ncia de:\s*)([\d\.]+,\d{2}|\d+\.\d{2})", re.I)
_RE_DATA = re.compile(r"(\d{2}/\d{2}/\d{4})")
_RE_TUSS = re.compile(r"(?:TUSS|codigo TUSS)[:\s]*(\d{8})", re.I)
_RE_SESSAO = re.compile(r"sess[a\u00e3]o n[.\s]*(?:\(ano civil\):?\s*)?(\d+)", re.I)

def _decodificar(base64_data: str) -> bytes:
    return base64.b64decode(base64_data)

def _texto_de_bytes(conteudo: bytes, mime_type: str) -> str:
    if mime_type.startswith("application/pdf") or conteudo[:4] == b"%PDF":
        doc = fitz.open(stream=conteudo, filetype="pdf")
        return "".join(p.get_text() for p in doc)
    imagem = Image.open(io.BytesIO(conteudo))
    return pytesseract.image_to_string(imagem, lang="por")

def _parse_valor(texto: str) -> Decimal | None:
    valores = []
    for m in _RE_VALOR.finditer(texto):
        bruto = m.group(1).replace(".", "").replace(",", ".")
        try:
            valores.append(Decimal(bruto))
        except Exception:
            continue
    if "valor total" in texto.lower():
        for m in re.finditer(r"valor total:\s*R\$\s*([\d\.]+,\d{2})", texto, re.I):
            bruto = m.group(1).replace(".", "").replace(",", ".")
            return Decimal(bruto)
    return max(valores) if valores else None

def _norm(texto: str) -> str:
    import unicodedata

    n = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in n if not unicodedata.combining(c))

def _classificar(texto: str) -> Categoria:
    lower = _norm(texto)
    # Classificacao pela natureza do documento.
    if any(
        k in lower
        for k in (
            "energia eletrica",
            "conta de consumo",
            "kwh",
            "fatura de energia",
            "bandeira tarifaria",
        )
    ):
        return Categoria.INVALIDO
    if any(
        k in lower
        for k in (
            "protese",
            "ortese",
            "opme",
            "componente tibial",
            "artroplastia",
            "material implantado",
        )
    ):
        return Categoria.MATERIAL_OPME
    # Procedimentos esteticos (Anexo IV): DESPESA_NAO_COBERTA.
    if any(
        k in lower
        for k in (
            "procedimento estetico",
            "cirurgia estetica",
            "botox estetico",
            "harmonizacao facial",
            "lipoaspiracao estetica",
            "finalidade estetica",
            "clinica de estetica",
        )
    ):
        return Categoria.DESPESA_NAO_COBERTA
    # Terapia tem precedencia sobre mencao incidental a relatorio.
    if any(
        k in lower
        for k in (
            "psicoterapia",
            "sessao de terapia",
            "fonoaudiologia",
            "terapia ocupacional",
            "fisioterapia",
            "psicologia clinica",
        )
    ) and ("recibo" in lower or "sessao" in lower or "tuSS" in texto.lower() or "tuss" in lower):
        return Categoria.SESSAO_TERAPIA
    if lower.strip().startswith("relatorio") or "relatorio clinico circunstanciado" in lower:
        return Categoria.RELATORIO_CLINICO
    if "relatorio clinico" in lower and "recibo" not in lower and "nao acompanha" not in lower:
        return Categoria.RELATORIO_CLINICO
    if "consulta" in lower and any(k in lower for k in ("medic", "dermatolog", "crm")):
        return Categoria.CONSULTA_MEDICA
    if "exame" in lower or "laboratorial" in lower:
        return Categoria.EXAME_DIAGNOSTICO
    if "recibo" in lower or "nota fiscal" in lower:
        return Categoria.CONSULTA_MEDICA
    return Categoria.INVALIDO

def extrair_anexo(base64_data: str, mime_type: str, filename: str = "") -> DocumentoExtraido:
    conteudo = _decodificar(base64_data)
    texto = _texto_de_bytes(conteudo, mime_type)
    categoria = _classificar(texto)

    doc = DocumentoExtraido(texto=texto, categoria=categoria)
    doc.valor_pago = _parse_valor(texto)

    datas = _RE_DATA.findall(texto)
    for d in datas:
        idx = texto.lower().find(d)
        trecho = texto[max(0, idx - 40):idx].lower()
        if "atendimento" in trecho or not doc.data_atendimento:
            doc.data_atendimento = d
            break
    if not doc.data_atendimento and datas:
        doc.data_atendimento = datas[0]

    if m := _RE_TUSS.search(texto):
        doc.codigo_tuss = m.group(1)

    if m := re.search(r"(?:recebi de|paciente):\s*([^\n]+)", texto, re.I):
        doc.nome_beneficiario = m.group(1).strip()

    if m := _RE_SESSAO.search(texto):
        doc.sessao_ano = int(m.group(1))
    else:
        m2 = re.search(r"sess[o\u00f5]es realizadas no ano civil:\s*(\d+)", texto, re.I)
        if m2:
            doc.sessao_ano = int(m2.group(1))

    if categoria == Categoria.INVALIDO:
        doc.invalido_motivo = "Arquivo nao e documento fiscal de despesa assistencial."

    return doc
