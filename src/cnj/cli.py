"""CLI: cnj descobrir | buscar | enriquecer | exportar."""

import argparse
import csv
import json
import logging
import os
import sys
import tomllib
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

from . import esaj
from .datajud import DataJud, _achatar, montar_query, normalizar_hit
from .filtros import (autores_aceitos, buscar_palavras, classificar_fase, data_referencia,
                      foro_origem, formatar_cnj)
from .http import Cliente, Limites, LimiteExcedido
from .store import Store

log = logging.getLogger("cnj")

EXEMPLOS = ["0174016-58.2018.8.06.0001", "0140003-43.2012.8.06.0001"]
PJE_CONSULTA = "https://pje-consulta.tjce.jus.br/pje1grau/ConsultaPublica/listView.seam"


def _carregar_env(caminho: Path) -> None:
    """Lê linhas CHAVE=valor de um .env (se existir) sem sobrescrever variáveis já definidas."""
    if not caminho.exists():
        return
    for linha in caminho.read_text().splitlines():
        linha = linha.strip()
        if linha and not linha.startswith("#") and "=" in linha:
            chave, valor = linha.split("=", 1)
            os.environ.setdefault(chave.strip(), valor.strip().strip("\"'"))


class Contexto:
    def __init__(self, caminho_config: Path):
        self.cfg = tomllib.loads(caminho_config.read_text())
        base = caminho_config.parent
        _carregar_env(base / ".env")
        self.store = Store(base / self.cfg["saida"]["banco"])
        lim = self.cfg["limites"]
        self.cliente = Cliente(self.store, Limites(
            intervalos={
                urlsplit(self.cfg["datajud"]["url"]).hostname: lim["intervalo_datajud"],
            },
            intervalo_padrao=lim["intervalo_portais"],
            jitter=lim["jitter"],
            backoff_base=lim["backoff_base"],
            backoff_max=lim["backoff_max"],
            max_tentativas=lim["max_tentativas"],
            timeout=lim["timeout"],
            fator_lentidao=lim.get("fator_lentidao", 2.0),
            recuperacao=lim.get("recuperacao", 0.95),
            intervalo_max=lim.get("intervalo_max", 120.0),
            tls_legado=tuple(lim.get("tls_legado", [])),
        ))
        self.datajud = DataJud(self.cliente, self.cfg["datajud"])

    def close(self):
        self.cliente.close()
        self.store.close()


def cmd_descobrir(ctx: Contexto, args):
    print("== Processos de exemplo no DataJud")
    for hit in ctx.datajud.por_numeros(args.numeros or EXEMPLOS):
        p, movs = normalizar_hit(hit, ctx.cfg["cumprimento"])
        print(f"\n{formatar_cnj(p['numero'])}  [{hit['_id']}]")
        print(f"  classe:   {p['classe_codigo']} {p['classe_nome']}")
        print(f"  sistema:  {p['sistema']} / {p['formato']} / grau {p['grau']}")
        print(f"  órgão:    {p['orgao']}")
        for a in json.loads(p["assuntos"]):
            print(f"  assunto:  {a['codigo']} {a['nome']}")
        print(f"  fase:     {p['fase']} ({p['evidencia'] or 'sem evidência'})")
        print(f"  {len(movs)} movimentos; códigos distintos:")
        for (codigo, nome), n in Counter((m["codigo"], m["nome"]) for m in movs).most_common():
            print(f"    {codigo:>6} x{n:<3} {nome}")

    print("\n== Assuntos mais frequentes nas ACPs do TJCE com termos ambientais/urbanísticos")
    query = {"bool": {"filter": [
        {"terms": {"classe.codigo": ctx.cfg["busca"]["classes"]}},
        {"match": {"assuntos.nome": "ambiental preservação permanente loteamento ocupação "
                                    "parcelamento urbanística florestal"}},
    ]}}
    aggs = ctx.datajud.agregar(query, ["assuntos.codigo"], tamanho=args.top)
    for b in aggs.get("assuntos.codigo", {}).get("buckets", []):
        exemplo = ctx.datajud.buscar(
            {"size": 1, "_source": ["assuntos"], "query": {"term": {"assuntos.codigo": b["key"]}}}
        )["hits"]["hits"]
        nomes = {a.get("nome") for a in _achatar(exemplo[0]["_source"].get("assuntos")) if a.get("codigo") == b["key"]} if exemplo else set()
        print(f"  {b['key']:>6}  {b['doc_count']:>6} processos  {', '.join(n for n in nomes if n)}")
    print("\nAjuste [busca] e [cumprimento] em config.toml com os códigos acima.")


def cmd_buscar(ctx: Contexto, args):
    total = 0
    try:
        if args.numeros:
            hits = ctx.datajud.por_numeros(args.numeros)
        else:
            query = montar_query(ctx.cfg["busca"])
            log.info("query: %s", json.dumps(query, ensure_ascii=False))
            hits = ctx.datajud.paginar(query, ctx.store, usar_cache=not args.refresh)
        for hit in hits:
            p, movs = normalizar_hit(hit, ctx.cfg["cumprimento"])
            ctx.store.salvar_processo(p, movs)
            _reclassificar(ctx, p["numero"])  # considera também movimentos já vindos do e-SAJ
            total += 1
            if total % 500 == 0:
                log.info("%d processos salvos", total)
    except LimiteExcedido as e:
        log.error("%s\nO progresso foi salvo; rode `cnj buscar` de novo mais tarde para retomar.", e)
        return 2
    cumprimento = len(ctx.store.processos(somente_cumprimento=True))
    print(f"{total} processos salvos nesta execução; {cumprimento} no banco em cumprimento de sentença.")
    return 0


def _reclassificar(ctx: Contexto, numero: str):
    movs = ctx.store.movimentos(numero)
    linhas = ctx.store.db.execute(
        "SELECT id, classe_codigo FROM processos WHERE numero = ?", (numero,)).fetchall()
    for p in linhas:
        ctx.store.atualizar_fase(p["id"], *classificar_fase(p["classe_codigo"], movs, ctx.cfg["cumprimento"]))


def _no_recorte(p: dict, filtro: dict) -> bool:
    """Foro de origem e ano do trânsito dentro do [filtro] (o autor é checado à parte)."""
    if filtro.get("foros") and foro_origem(p["numero"]) not in filtro["foros"]:
        return False
    data = data_referencia(p)
    if not data:
        return False
    ano = int(data[:4])
    return ano >= filtro.get("transito_desde", 0) and (
        not filtro.get("transito_ate") or ano <= filtro["transito_ate"])


def cmd_enriquecer(ctx: Contexto, args):
    filtro = ctx.cfg.get("filtro", {})
    numeros = sorted({p["numero"] for p in ctx.store.processos(somente_cumprimento=not args.todas)
                      if args.todas or _no_recorte(p, filtro)})
    if args.numeros:
        numeros = [n.replace("-", "").replace(".", "") for n in args.numeros]
    pendentes = [n for n in numeros if args.refresh or ctx.store.esaj(n) is None]
    log.info("%d processos para consultar no e-SAJ (%d já consultados)", len(pendentes),
             len(numeros) - len(pendentes))
    for i, numero in enumerate(pendentes, 1):
        try:
            dados = esaj.consultar(ctx.cliente, numero)
        except LimiteExcedido as e:
            log.error("%s\nRode `cnj enriquecer` de novo para continuar.", e)
            return 2
        ctx.store.salvar_esaj(numero, dados)
        _reclassificar(ctx, numero)
        estado = "não encontrado" if dados is None else (
            "migrado p/ PJe" if dados["migrado_pje"] else dados["situacao"] or "ok")
        log.info("[%d/%d] %s: %s", i, len(pendentes), formatar_cnj(numero), estado)
    return 0


COLUNAS = [
    "numero", "grau", "classe", "assuntos", "orgao", "data_ajuizamento", "fase", "evidencia",
    "data_transito", "autor_mp_dp", "sistema_datajud", "esaj_situacao", "migrado_pje", "partes",
    "palavras_encontradas", "link_esaj", "link_pje",
]


def _linhas(ctx: Contexto, args, descartes: Counter):
    palavras = [p.strip() for p in (args.palavras or "").split(",") if p.strip()]
    filtro = {} if args.sem_filtro else ctx.cfg.get("filtro", {})
    vistos = set()
    for p in ctx.store.processos(somente_cumprimento=not args.todas):
        # O DataJud às vezes tem o mesmo processo com dois _id (ex.: sistema "Pje" e "PJe").
        if (p["numero"], p["grau"]) in vistos:
            continue
        vistos.add((p["numero"], p["grau"]))
        if filtro and foro_origem(p["numero"]) not in filtro.get("foros", [foro_origem(p["numero"])]):
            descartes["fora de Fortaleza (foro)"] += 1
            continue
        if filtro and not _no_recorte(p, filtro):
            descartes["trânsito fora do período"] += 1
            continue
        e = ctx.store.esaj(p["numero"])
        encontrado = bool(e and e["encontrado"])
        partes = json.loads(e["partes"] or "[]") if encontrado else []
        autores = autores_aceitos(partes, ctx.cfg.get("filtro", {}))
        if filtro and filtro.get("autores_trechos") and not autores:
            if partes:
                descartes["autor não é MP/Defensoria"] += 1
                continue
            if not args.sem_partes:
                descartes["sem partes no e-SAJ (use --sem-partes)"] += 1
                continue
        achados = buscar_palavras(p, ctx.store.movimentos(p["numero"]), e, palavras)
        if palavras and not achados:
            descartes["sem as palavras-chave"] += 1
            continue
        yield {
            "numero": formatar_cnj(p["numero"]),
            "grau": p["grau"],
            "classe": f"{p['classe_codigo']} {p['classe_nome']}",
            "assuntos": "; ".join(a["nome"] or "" for a in json.loads(p["assuntos"])),
            "orgao": p["orgao"],
            "data_ajuizamento": (p["data_ajuizamento"] or "")[:8],
            "fase": p["fase"],
            "evidencia": p["evidencia"],
            "data_transito": p["data_transito"],
            "autor_mp_dp": "; ".join(dict.fromkeys(autores)) or ("" if partes else "não verificado"),
            "sistema_datajud": p["sistema"],
            "esaj_situacao": (e["situacao"] if encontrado else "não encontrado") if e else "não consultado",
            "migrado_pje": "sim" if encontrado and e["migrado_pje"] else "",
            "partes": "; ".join(f"{x['tipo']}: {x['nome']}" for x in partes),
            "palavras_encontradas": "; ".join(f"{k} ({', '.join(v)})" for k, v in achados.items()),
            "link_esaj": e["url"] if encontrado else esaj.url_busca(p["numero"]),
            "link_pje": PJE_CONSULTA,
        }


def cmd_exportar(ctx: Contexto, args):
    descartes = Counter()
    linhas = list(_linhas(ctx, args, descartes))
    saida = Path(args.saida)
    saida.parent.mkdir(parents=True, exist_ok=True)
    if saida.suffix.lower() == ".csv":
        with saida.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=COLUNAS)
            w.writeheader()
            w.writerows(linhas)
    else:
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "processos"
        ws.append(COLUNAS)
        for linha in linhas:
            ws.append([linha[c] for c in COLUNAS])
        for i, linha in enumerate(linhas, 2):
            ws.cell(i, COLUNAS.index("link_esaj") + 1).hyperlink = linha["link_esaj"]
        ws.freeze_panes = "B2"
        ws.auto_filter.ref = ws.dimensions
        wb.save(saida)
    print(f"{len(linhas)} processos exportados para {saida}")
    for motivo, n in descartes.most_common():
        print(f"  {n:>4} descartados: {motivo}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="cnj", description=__doc__)
    ap.add_argument("--config", type=Path, default=Path("config.toml"))
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("descobrir", help="mostra classes/assuntos/movimentos reais para calibrar o config")
    s.add_argument("numeros", nargs="*", help=f"números CNJ (padrão: {', '.join(EXEMPLOS)})")
    s.add_argument("--top", type=int, default=30)
    s.set_defaults(fn=cmd_descobrir)

    s = sub.add_parser("buscar", help="baixa do DataJud as ACPs com assuntos ambientais")
    s.add_argument("--refresh", action="store_true", help="ignora o cache HTTP")
    s.add_argument("--numeros", nargs="+", help="salva só estes processos, qualquer classe/assunto")
    s.set_defaults(fn=cmd_buscar)

    s = sub.add_parser("enriquecer", help="consulta o e-SAJ público (partes, texto das movimentações)")
    s.add_argument("numeros", nargs="*", help="só estes processos")
    s.add_argument("--todas", action="store_true", help="inclui processos fora do cumprimento")
    s.add_argument("--refresh", action="store_true", help="consulta de novo os já consultados")
    s.set_defaults(fn=cmd_enriquecer)

    s = sub.add_parser("exportar", help="gera planilha xlsx/csv")
    s.add_argument("--palavras", help="lista separada por vírgulas; mantém só quem tiver alguma")
    s.add_argument("--todas", action="store_true", help="inclui processos fora do cumprimento")
    s.add_argument("--sem-filtro", action="store_true",
                   help="ignora [filtro] do config (foro, autor, ano do trânsito)")
    s.add_argument("--sem-partes", action="store_true",
                   help="inclui processos sem partes no e-SAJ (autor não verificado)")
    s.add_argument("--saida", default="data/processos.xlsx")
    s.set_defaults(fn=cmd_exportar)

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ctx = Contexto(args.config)
    try:
        return args.fn(ctx, args) or 0
    except KeyboardInterrupt:
        log.warning("interrompido; o progresso salvo será reaproveitado na próxima execução")
        return 130
    finally:
        ctx.close()


if __name__ == "__main__":
    sys.exit(main())
