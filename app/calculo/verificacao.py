"""Verificacao de elegibilidade ANTES do calculo do valor.

A devolutiva da banca: o no que calcula nao pode rodar antes do veredito.
Estado incompleto vira erro/pendencia - nunca aprovacao com defaults.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from app.schemas import Categoria, Decisao

ALCADA_AMB = Decimal("5000")


@dataclass
class Veredito:
    status: str  # OK | BLOQUEADO | INCOMPLETO
    decisao: Decisao | None = None
    regras: list[str] = field(default_factory=list)
    pendencias: list[str] = field(default_factory=list)
    escalonar: bool = False
    motivo: str = ""


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


def _psico_sess_rel(atendimento: date) -> tuple[Decimal, Decimal, int, str | None]:
    """Espelha a vigencia de tetos/relatorio da Circular 02/2026 em diante."""
    if atendimento >= date(2026, 4, 20):
        return Decimal("2.8"), Decimal("2.6"), 5, "CIRC-02-2026"
    if atendimento >= date(2026, 2, 1):
        return Decimal("1.6"), Decimal("1.4"), 10, "CIRC-11-2026"
    return Decimal("1.2"), Decimal("1.0"), 24, None


def verificar_elegibilidade(
    *,
    categoria: Categoria | str | None,
    valor_pago: Decimal | None,
    data_atendimento: str | None,
    plano: str | None,
    data_adesao: str | None,
    historico: list | None = None,
    sessao_ano: int | None = None,
    tem_relatorio: bool = False,
    data_protocolo: str | date | None = None,
    status: str | None = None,
    codigo_tuss: str | None = None,
) -> Veredito:
    """Escreve o veredito de apuracao. So status=OK autoriza o calculo do valor."""
    pend: list[str] = []

    if categoria is None:
        return Veredito(
            "INCOMPLETO",
            pendencias=["categoria_documento"],
            motivo="categoria ausente",
        )
    if isinstance(categoria, str):
        try:
            categoria = Categoria(categoria)
        except ValueError:
            return Veredito(
                "INCOMPLETO",
                pendencias=["categoria_documento invalida"],
                motivo="categoria invalida",
            )

    if categoria == Categoria.INVALIDO:
        return Veredito(
            "BLOQUEADO",
            Decisao.PENDENTE_DOCUMENTO,
            ["ART-76"],
            ["documento fiscal de despesa assistencial"],
            motivo="documento invalido",
        )

    if categoria == Categoria.DESPESA_NAO_COBERTA:
        return Veredito(
            "BLOQUEADO",
            Decisao.NEGADO,
            ["ANEXO-IV", "ART-33"],
            motivo="despesa nao coberta",
        )

    if categoria == Categoria.MATERIAL_OPME:
        return Veredito(
            "BLOQUEADO",
            Decisao.ESCALADO_ANALISTA,
            ["ART-78"],
            escalonar=True,
            motivo="OPME exige analista",
        )

    if not status:
        pend.append("status_contrato")
    if not data_adesao or _parse_date(data_adesao) is None:
        pend.append("data_adesao")
    if not data_atendimento or _parse_date(data_atendimento) is None:
        pend.append("data_atendimento")
    if valor_pago is None:
        pend.append("valor_pago")
    if not plano:
        pend.append("plano")

    if pend:
        return Veredito(
            "INCOMPLETO",
            Decisao.PENDENTE_DOCUMENTO,
            pendencias=pend,
            motivo="estado incompleto para decidir",
        )

    status_u = str(status).upper()
    if status_u not in {"ATIVO", "ACTIVE"}:
        return Veredito(
            "BLOQUEADO",
            Decisao.NEGADO,
            ["ART-30"],
            motivo="contrato inativo",
        )

    atendimento = _parse_date(data_atendimento)
    adesao = _parse_date(data_adesao)
    assert atendimento is not None and adesao is not None

    if isinstance(data_protocolo, date):
        protocolo_dt = data_protocolo
    else:
        protocolo_dt = _parse_date(str(data_protocolo) if data_protocolo else None) or date(
            2026, 5, 20
        )

    prazo = 150 if atendimento >= date(2025, 7, 1) else 90
    if (protocolo_dt - atendimento).days > prazo:
        r = ["ART-12"]
        if atendimento >= date(2025, 7, 1):
            r.append("CIRC-04-2025")
        return Veredito("BLOQUEADO", Decisao.NEGADO, r, motivo="fora do prazo")

    valor = Decimal(str(valor_pago))
    if valor > ALCADA_AMB:
        return Veredito(
            "BLOQUEADO",
            Decisao.ESCALADO_ANALISTA,
            ["ART-78"],
            escalonar=True,
            motivo="acima da alcada automatizada",
        )

    dias_adesao = (atendimento - adesao).days
    if categoria == Categoria.CONSULTA_MEDICA and dias_adesao < 15:
        return Veredito("BLOQUEADO", Decisao.NEGADO, ["ART-22"], motivo="carencia consulta")
    if categoria == Categoria.SESSAO_TERAPIA and dias_adesao < 150:
        return Veredito("BLOQUEADO", Decisao.NEGADO, ["ART-24"], motivo="carencia terapia")
    if categoria == Categoria.EXAME_DIAGNOSTICO and dias_adesao < 60:
        return Veredito("BLOQUEADO", Decisao.NEGADO, ["ART-23"], motivo="carencia exame")

    if categoria == Categoria.SESSAO_TERAPIA:
        teto_urs, cont_urs, sess_rel, circ = _psico_sess_rel(atendimento)
        anteriores = len(historico or [])
        if sessao_ano is not None:
            anteriores = max(anteriores, max(0, int(sessao_ano) - 1))
        sessao_atual = anteriores + 1
        if anteriores >= 8:
            teto = cont_urs * Decimal("95.10")
        else:
            teto = teto_urs * Decimal("95.10")
        regras = ["ART-41"]
        if circ:
            regras.append(circ)
        regras.extend(["ART-33", "ART-73"])
        if codigo_tuss:
            regras.append(f"TUSS-{codigo_tuss}")
        precisa_rel = int(sessao_atual) >= sess_rel or valor > teto * Decimal("1.75")
        if precisa_rel and not tem_relatorio:
            return Veredito(
                "BLOQUEADO",
                Decisao.PENDENTE_DOCUMENTO,
                regras,
                ["relatorio clinico circunstanciado do profissional assistente"],
                motivo="falta relatorio clinico",
            )
        if int(sessao_atual) > 40:
            return Veredito("BLOQUEADO", Decisao.NEGADO, ["ART-40"], motivo="limite sessoes")

    return Veredito("OK", motivo="apto para calculo")
