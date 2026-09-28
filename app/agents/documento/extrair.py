"""Leitura e classificação de anexos."""

from __future__ import annotations

import base64
import io
import re
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.schemas import Categoria


class DocumentoExtraido(BaseModel):
    categoria: Categoria
    texto: str = ""
    nome_beneficiario: str | None = None
    cpf_beneficiario: str | None = None
    valor_pago: Decimal | None = None
    data_atendimento: str | None = None
    codigo_tuss: str | None = None
    descricao: str | None = None
    prestador: str | None = None
    registro_conselho: str | None = None
    numero_sessao: int | None = None
    sessao_informada: bool = False
    campos_faltantes: list[str] = Field(default_factory=list)
    tem_indicacao_clinica: bool = False
    itens_opme: bool = False


def _decodificar(anexo: dict) -> tuple[bytes, str]:
    raw = anexo.get("base64") or ""
    data = base64.b64decode(raw)
    nome = (anexo.get("filename") or "anexo.bin").lower()
    mime = (anexo.get("mime_type") or "").lower()
    return data, nome if nome else mime


def ler_texto(anexo: dict) -> str:
    data, nome = _decodificar(anexo)
    if nome.endswith(".pdf") or "pdf" in nome:
        import fitz

        with fitz.open(stream=data, filetype="pdf") as doc:
            texto = "".join(p.get_text() for p in doc)
        if texto.strip():
            return texto
        # OCR fallback para PDF escaneado
        try:
            import fitz
            import pytesseract
            from PIL import Image

            partes = []
            with fitz.open(stream=data, filetype="pdf") as doc:
                for page in doc:
                    pix = page.get_pixmap(dpi=200)
                    img = Image.open(io.BytesIO(pix.tobytes("png")))
                    partes.append(pytesseract.image_to_string(img, lang="por"))
            return "\n".join(partes)
        except Exception:
            return texto
    if any(nome.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff")):
        try:
            import pytesseract
            from PIL import Image

            img = Image.open(io.BytesIO(data))
            return pytesseract.image_to_string(img, lang="por")
        except Exception:
            return ""
    if nome.endswith(".docx"):
        from docx import Document

        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
            tmp.write(data)
            path = tmp.name
        try:
            return "\n".join(p.text for p in Document(path).paragraphs)
        finally:
            Path(path).unlink(missing_ok=True)
    try:
        return data.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _parse_valor(texto: str) -> Decimal | None:
    # Preferência por "R$ 240,00" / "Valor total: R$ 9.200,00"
    candidatos = re.findall(
        r"R\$\s*([\d.]+,\d{2})", texto, flags=re.IGNORECASE
    )
    if not candidatos:
        candidatos = re.findall(r"([\d.]+,\d{2})", texto)
    if not candidatos:
        return None
    # Em geral o maior valor destacado / o último "total"
    if re.search(r"valor\s+total", texto, re.IGNORECASE):
        m = re.search(r"valor\s+total[:\s]*R\$\s*([\d.]+,\d{2})", texto, re.IGNORECASE)
        if m:
            return Decimal(m.group(1).replace(".", "").replace(",", "."))
    vals = [Decimal(c.replace(".", "").replace(",", ".")) for c in candidatos]
    # Evita pegar tarifa unitária pequena: se houver valores > 50, usa o maior razoável
    grandes = [v for v in vals if v >= 50]
    if grandes:
        return max(grandes)
    return vals[-1]


def _parse_data(texto: str) -> str | None:
    m = re.search(
        r"Data do atendimento[:\s]*(\d{2}/\d{2}/\d{4})", texto, re.IGNORECASE
    )
    if m:
        d, mth, y = m.group(1).split("/")
        return f"{y}-{mth}-{d}"
    m = re.search(r"(\d{2}/\d{2}/\d{4})", texto)
    if m:
        d, mth, y = m.group(1).split("/")
        return f"{y}-{mth}-{d}"
    return None


def _parse_tuss(texto: str) -> str | None:
    m = re.search(r"(?:Código\s+)?TUSS[:\s]*(\d{8})", texto, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"\b(\d{8})\b", texto)
    return m.group(1) if m else None


def classificar(texto: str) -> Categoria:
    t = texto.lower()
    # Inválido: conta de consumo / não assistencial
    if any(
        k in t
        for k in (
            "energia elétrica",
            "energia eletrica",
            "fatura de energia",
            "consumo em kwh",
            "comprovante bancário",
            "comprovante bancario",
            "cupom fiscal de supermercado",
        )
    ) and not any(k in t for k in ("tuss", "crm", "crp", "consulta", "sessão", "sessao", "prótese", "protese")):
        return Categoria.INVALIDO

    if any(k in t for k in ("prótese", "protese", "órtese", "ortese", "opme", "material implantado")):
        return Categoria.MATERIAL_OPME
    if "relatório clínico" in t or "relatorio clinico" in t or "circunstanciado" in t:
        return Categoria.RELATORIO_CLINICO
    if any(k in t for k in ("psicoterapia", "sessão de terapia", "sessao de terapia", "fonoaudiologia", "terapia ocupacional", "fisioterapia")):
        return Categoria.SESSAO_TERAPIA
    if any(k in t for k in ("exame", "laboratorial", "tomografia", "ressonância", "ultrasson")):
        return Categoria.EXAME_DIAGNOSTICO
    if any(k in t for k in ("consulta médica", "consulta medica", "dermatolog", "consultório", "consultorio")):
        return Categoria.CONSULTA_MEDICA
    if "recibo" in t and "médic" in t:
        return Categoria.CONSULTA_MEDICA
    # Exclusões estéticas óbvias
    if any(k in t for k in ("botox estético", "harmonização", "lipoaspiração", "peeling")):
        return Categoria.DESPESA_NAO_COBERTA
    if not any(k in t for k in ("tuss", "crm", "crp", "recibo", "nota fiscal", "prestação de serviços")):
        return Categoria.INVALIDO
    return Categoria.CONSULTA_MEDICA


def extrair(anexo: dict) -> DocumentoExtraido:
    texto = ler_texto(anexo)
    cat = classificar(texto)
    valor = _parse_valor(texto) if cat != Categoria.INVALIDO else None
    data = _parse_data(texto)
    tuss = _parse_tuss(texto)

    nome = None
    m = re.search(r"(?:Recebi de|Paciente)[:\s]+([A-Za-zÀ-ÿ ]{5,80})", texto)
    if m:
        nome = m.group(1).strip()

    cpf = None
    m = re.search(r"CPF do paciente[:\s]*([\d.\-]+)", texto, re.IGNORECASE)
    if m:
        cpf = m.group(1)

    num_sessao = None
    sessao_inf = False
    m = re.search(r"Sessão n[ºo°]?[^:]*:\s*(\d+)", texto, re.IGNORECASE)
    if m:
        num_sessao = int(m.group(1))
        sessao_inf = True
    elif re.search(r"Sessão n[ºo°]?[^:]*:\s*não informado", texto, re.IGNORECASE):
        sessao_inf = False

    faltantes: list[str] = []
    if cat not in {Categoria.INVALIDO, Categoria.RELATORIO_CLINICO}:
        if not nome:
            faltantes.append("identificacao_beneficiario")
        if not data:
            faltantes.append("data_atendimento")
        if valor is None:
            faltantes.append("valor")
        if not tuss and cat != Categoria.MATERIAL_OPME:
            # descrição pode bastar; não força TUSS como pendência dura
            pass

    return DocumentoExtraido(
        categoria=cat,
        texto=texto[:8000],
        nome_beneficiario=nome,
        cpf_beneficiario=cpf,
        valor_pago=valor,
        data_atendimento=data,
        codigo_tuss=tuss,
        descricao=_primeira_linha_procedimento(texto),
        numero_sessao=num_sessao,
        sessao_informada=sessao_inf,
        campos_faltantes=faltantes,
        tem_indicacao_clinica=bool(
            re.search(r"indica[cç][aã]o cl[ií]nica", texto, re.IGNORECASE)
        ),
        itens_opme=cat == Categoria.MATERIAL_OPME,
    )


def _primeira_linha_procedimento(texto: str) -> str | None:
    m = re.search(r"Referente a:\s*(.+)", texto, re.IGNORECASE)
    if m:
        return m.group(1).strip()[:200]
    return None
