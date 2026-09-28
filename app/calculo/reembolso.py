"""Motor de apuracao deterministica do reembolso.

Ordem obrigatoria: verificacao escreve o veredito; so entao o valor e calculado.
Estado incompleto nao aprova (sem defaults otimistas de data/adesao).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from app.calculo.verificacao import verificar_elegibilidade
from app.schemas import Categoria, Decisao

URS_2026 = Decimal("95.10")
LIMITE_ANUAL_URS = Decimal("48")
CONSULTA_URS = Decimal("5")
ALCADA_AMB = Decimal("5000")

_COPART = [
    (
        date(2026, 4, 20),
        {
            "Pleno": [Decimal("0.40"), Decimal("0.30"), Decimal("0.20")],
            "Essencial": [Decimal("0.45"), Decimal("0.35"), Decimal("0.25")],
        },
    ),
    (
        date(1900, 1, 1),
        {
            "Pleno": [Decimal("0.30"), Decimal("0.20"), Decimal("0.10")],
            "Essencial": [Decimal("0.35"), Decimal("0.25"), Decimal("0.15")],
        },
    ),
]

_PSICO = [
    (date(2026, 4, 20), Decimal("2.8"), Decimal("2.6"), 5, "CIRC-02-2026"),
    (date(2026, 2, 1), Decimal("1.6"), Decimal("1.4"), 10, "CIRC-11-2026"),
    (date(1900, 1, 1), Decimal("1.2"), Decimal("1.0"), 24, None),
]


def _money(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


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


def _meses(adesao: date, ref: date) -> int:
    m = (ref.year - adesao.year) * 12 + (ref.month - adesao.month)
    if ref.day < adesao.day:
        m -= 1
    return max(0, m)


def _reembolsado_ano(historico: list | None) -> Decimal:
    total = Decimal("0")
    for p in historico or []:
        v = p.get("valor_reembolsado_brl", p.get("valor_reembolso_brl"))
        if v is not None:
            total += Decimal(str(v))
    return total


def _copart(plano: str, adesao: date, atendimento: date) -> tuple[Decimal, list[str]]:
    tabela = _COPART[-1][1]
    for inicio, tab in _COPART:
        if atendimento >= inicio:
            tabela = tab
            break
    plano_n = (plano or "Essencial").strip().title()
    if plano_n not in tabela:
        plano_n = "Essencial"
    faixas = tabela[plano_n]
    meses = _meses(adesao, atendimento)
    if meses < 12:
        pct = faixas[0]
    elif meses <= 36:
        pct = faixas[1]
    else:
        pct = faixas[2]
    return pct, ["ART-44"]


def _psico(atendimento: date):
    for inicio, teto, cont, rel, circ in _PSICO:
        if atendimento >= inicio:
            return teto, cont, rel, circ
    return Decimal("1.2"), Decimal("1.0"), 24, None


@dataclass
class ResultadoReembolso:
    decisao: Decisao
    valor_solicitado: Decimal
    valor_reembolso: Decimal | None
    regras: list[str] = field(default_factory=list)
    pendencias: list[str] = field(default_factory=list)
    escalonar: bool = False
    detalhe: dict = field(default_factory=dict)


def calcular_reembolso(
    *,
    categoria: Categoria | str,
    valor_pago: Decimal | None,
    codigo_tuss: str | None,
    data_atendimento: str | None,
    plano: str | None,
    data_adesao: str | None,
    historico: list | None = None,
    sessao_ano: int | None = None,
    tem_relatorio: bool = False,
    data_protocolo: str | date | None = None,
    status: str | None = "ATIVO",
) -> ResultadoReembolso:
    """Verifica primeiro; so calcula valor se o veredito for OK."""
    veredito = verificar_elegibilidade(
        categoria=categoria,
        valor_pago=valor_pago,
        data_atendimento=data_atendimento,
        plano=plano,
        data_adesao=data_adesao,
        historico=historico,
        sessao_ano=sessao_ano,
        tem_relatorio=tem_relatorio,
        data_protocolo=data_protocolo,
        status=status,
        codigo_tuss=codigo_tuss,
    )

    sol = _money(Decimal(str(valor_pago))) if valor_pago is not None else Decimal("0")

    if veredito.status == "INCOMPLETO":
        return ResultadoReembolso(
            Decisao.PENDENTE_DOCUMENTO,
            sol,
            None,
            veredito.regras or ["ART-76"],
            pendencias=veredito.pendencias or ["dados insuficientes para apuracao"],
        )

    if veredito.status == "BLOQUEADO":
        return ResultadoReembolso(
            veredito.decisao or Decisao.NEGADO,
            sol,
            None,
            veredito.regras,
            pendencias=list(veredito.pendencias),
            escalonar=veredito.escalonar,
        )

    # --- Calculo somente apos verificacao OK ---
    if isinstance(categoria, str):
        categoria = Categoria(categoria)
    valor_pago_d = Decimal(str(valor_pago))
    atendimento = _parse_date(data_atendimento)
    adesao = _parse_date(data_adesao)
    if atendimento is None or adesao is None:
        return ResultadoReembolso(
            Decisao.PENDENTE_DOCUMENTO,
            sol,
            None,
            ["ART-76"],
            pendencias=["data_atendimento ou data_adesao invalida"],
        )

    regras: list[str] = []
    sessao_atual = sessao_ano

    if categoria == Categoria.CONSULTA_MEDICA:
        teto = CONSULTA_URS * URS_2026
        regras.extend(["ART-35", "ART-33"])
        if codigo_tuss:
            regras.append(f"TUSS-{codigo_tuss}")
    elif categoria == Categoria.SESSAO_TERAPIA:
        teto_urs, cont_urs, _sess_rel, circ = _psico(atendimento)
        anteriores = len(historico or [])
        if sessao_ano is not None:
            anteriores = max(anteriores, max(0, int(sessao_ano) - 1))
        sessao_atual = anteriores + 1
        if anteriores >= 8:
            teto = cont_urs * URS_2026
        else:
            teto = teto_urs * URS_2026
        regras.append("ART-41")
        if circ:
            regras.append(circ)
        regras.append("ART-33")
        if codigo_tuss:
            regras.append(f"TUSS-{codigo_tuss}")
        regras.append("ART-73")
    else:
        teto = valor_pago_d
        regras.append("ART-33")
        if codigo_tuss:
            regras.append(f"TUSS-{codigo_tuss}")

    regras.append("ART-43")
    base = min(valor_pago_d, teto)
    teto_cortou = valor_pago_d > teto

    pct, r_cop = _copart(plano or "Essencial", adesao, atendimento)
    regras.extend(r_cop)
    apos = base * (Decimal("1") - pct)

    regras.append("ART-45")
    reemb_ano = _reembolsado_ano(historico)
    saldo = LIMITE_ANUAL_URS * URS_2026 - reemb_ano
    if saldo <= 0:
        return ResultadoReembolso(
            Decisao.NEGADO, _money(valor_pago_d), None, regras + ["ART-47"]
        )

    anual_cortou = apos > saldo
    final = _money(min(apos, saldo))
    regras.append("ART-47")

    decisao = (
        Decisao.APROVADO_PARCIAL if (teto_cortou or anual_cortou) else Decisao.APROVADO
    )

    vistos: set[str] = set()
    ord_regras: list[str] = []
    for r in regras:
        if r not in vistos:
            vistos.add(r)
            ord_regras.append(r)

    return ResultadoReembolso(
        decisao=decisao,
        valor_solicitado=_money(valor_pago_d),
        valor_reembolso=final,
        regras=ord_regras,
        detalhe={
            "teto": float(_money(teto)),
            "copart": float(pct),
            "saldo": float(_money(saldo)),
            "sessao": sessao_atual,
        },
    )
