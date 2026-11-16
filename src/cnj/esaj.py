"""Consulta pública do e-SAJ TJCE (1º grau): dados, partes e texto das movimentações."""

import re
from urllib.parse import urlencode, urljoin

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from .filtros import formatar_cnj, so_digitos
from .http import Cliente

BASE = "https://esaj.tjce.jus.br/cpopg/"


def url_busca(numero: str) -> str:
    cnj = formatar_cnj(numero)
    n = so_digitos(numero)
    params = {
        "cbPesquisa": "NUMPROC",
        "numeroDigitoAnoUnificado": cnj[:15],
        "foroNumeroUnificado": n[16:],
        "dadosConsulta.valorConsultaNuUnificado": cnj,
        "dadosConsulta.tipoNuProcesso": "UNIFICADO",
    }
    return BASE + "search.do?" + urlencode(params)


def _texto(no) -> str:
    return re.sub(r"\s+", " ", no.text(separator=" ")).strip() if no else ""


def _data_iso(data_br: str) -> str | None:
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", data_br)
    return f"{m[3]}-{m[2]}-{m[1]}" if m else None


def parse_show(html: str, url: str) -> dict:
    doc = HTMLParser(html)

    def campo(id_):
        return _texto(doc.css_first(f"#{id_}"))

    movimentos = []
    tabela = doc.css_first("#tabelaTodasMovimentacoes") or doc.css_first("#tabelaUltimasMovimentacoes")
    for tr in tabela.css("tr.containerMovimentacao") if tabela else []:
        descricao = tr.css_first("td.descricaoMovimentacao")
        completo = _texto(descricao)
        detalhe = _texto(descricao.css_first("span")) if descricao else ""
        nome = completo[: len(completo) - len(detalhe)].strip() if detalhe and completo.endswith(detalhe) else completo
        movimentos.append({
            "data": _data_iso(_texto(tr.css_first("td.dataMovimentacao"))),
            "codigo": None,
            "nome": nome,
            "texto": detalhe or None,
        })

    partes = []
    tabela_partes = doc.css_first("#tableTodasPartes") or doc.css_first("#tablePartesPrincipais")
    for tr in tabela_partes.css("tr") if tabela_partes else []:
        tipo = _texto(tr.css_first(".tipoDeParticipacao"))
        nome = _texto(tr.css_first("td.nomeParteEAdvogado"))
        if tipo and nome:
            partes.append({"tipo": tipo, "nome": nome})

    return {
        "url": url,
        "classe": campo("classeProcesso"),
        "assunto": campo("assuntoProcesso"),
        "vara": campo("varaProcesso"),
        "juiz": campo("juizProcesso"),
        "situacao": campo("labelSituacaoProcesso"),
        "migrado_pje": any("migrado para o pje" in (m["nome"] + " " + (m["texto"] or "")).lower()
                           for m in movimentos),
        "partes": partes,
        "movimentos": movimentos,
    }


def consultar(cliente: Cliente, numero: str) -> dict | None:
    """Retorna os dados do processo no e-SAJ, ou None se não existir lá (ex.: nativo do PJe)."""
    r = cliente.get(url_busca(numero))
    url, html = r.url_final, r.texto
    if "show.do" not in url:
        # Lista de resultados (ex.: processo principal + incidentes): pega o link exato.
        doc = HTMLParser(html)
        alvo = formatar_cnj(numero)
        link = next((a for a in doc.css("a.linkProcesso") if alvo in _texto(a)), None)
        if link is None:
            return None
        url = urljoin(BASE, link.attributes.get("href", ""))
        r = cliente.get(url)
        url, html = r.url_final, r.texto
    if "show.do" not in url:
        return None
    return parse_show(html, url)
