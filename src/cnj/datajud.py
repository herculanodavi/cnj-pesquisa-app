"""Consultas à API Pública do DataJud (Elasticsearch)."""

import hashlib
import json
import logging
import os

from .filtros import classificar_fase, so_digitos
from .http import Cliente
from .store import Store

log = logging.getLogger(__name__)


class DataJud:
    def __init__(self, cliente: Cliente, cfg: dict):
        self.cliente = cliente
        self.url = cfg["url"]
        chave = os.environ.get("DATAJUD_API_KEY")
        if not chave:
            raise RuntimeError("Defina DATAJUD_API_KEY (no arquivo .env ou no ambiente); veja .env.example.")
        self.headers = {"Authorization": f"APIKey {chave}"}
        self.tamanho_pagina = cfg.get("tamanho_pagina", 500)

    def buscar(self, body: dict, usar_cache: bool = True) -> dict:
        r = self.cliente.post_json(self.url, body, headers=self.headers, usar_cache=usar_cache)
        if r.status != 200:
            raise RuntimeError(f"DataJud respondeu {r.status}: {r.texto[:500]}")
        return r.json()

    def por_numeros(self, numeros: list[str]) -> list[dict]:
        body = {"size": 50, "query": {"terms": {"numeroProcesso": [so_digitos(n) for n in numeros]}}}
        return self.buscar(body, usar_cache=False)["hits"]["hits"]

    def agregar(self, query: dict, campos: list[str], tamanho: int = 50) -> dict:
        body = {
            "size": 0,
            "query": query,
            "aggs": {c: {"terms": {"field": c, "size": tamanho}} for c in campos},
        }
        return self.buscar(body, usar_cache=False).get("aggregations", {})

    def paginar(self, query: dict, store: Store, usar_cache: bool = True):
        """Itera sobre todos os hits com search_after, salvando o cursor a cada página.

        Se a execução for interrompida, a próxima chamada com a mesma query retoma
        de onde parou.
        """
        nome = "busca:" + hashlib.sha256(json.dumps(query, sort_keys=True).encode()).hexdigest()[:16]
        cursor = store.cursor_get(nome)
        if cursor:
            log.info("retomando busca a partir do cursor salvo %s", cursor)
        while True:
            body = {"size": self.tamanho_pagina, "query": query, "sort": [{"@timestamp": "asc"}]}
            if cursor:
                body["search_after"] = cursor
            hits = self.buscar(body, usar_cache=usar_cache)["hits"]["hits"]
            if not hits:
                store.cursor_del(nome)
                return
            yield from hits
            cursor = hits[-1]["sort"]
            store.cursor_put(nome, cursor)


def montar_query(busca: dict) -> dict:
    assuntos = [{"match_phrase": {"assuntos.nome": n}} for n in busca.get("assuntos_nomes", [])]
    if busca.get("assuntos_codigos"):
        assuntos.append({"terms": {"assuntos.codigo": busca["assuntos_codigos"]}})
    filtros = [{"terms": {"classe.codigo": busca["classes"]}}]
    if assuntos:
        filtros.append({"bool": {"should": assuntos, "minimum_should_match": 1}})
    return {"bool": {"filter": filtros}}


def movimentos_de(source: dict) -> list[dict]:
    saida = []
    for m in source.get("movimentos") or []:
        complementos = "; ".join(
            f"{c.get('descricao') or c.get('nome') or ''}: {c.get('nome') or c.get('valor') or ''}"
            for c in m.get("complementosTabelados") or []
        )
        saida.append({
            "data": m.get("dataHora"),
            "codigo": m.get("codigo"),
            "nome": m.get("nome"),
            "texto": complementos or None,
        })
    return saida


def _achatar(itens) -> list[dict]:
    """Alguns tribunais mandam `assuntos` como lista de listas."""
    saida = []
    for item in itens or []:
        if isinstance(item, list):
            saida.extend(_achatar(item))
        elif isinstance(item, dict):
            saida.append(item)
    return saida


def normalizar_hit(hit: dict, regras_cumprimento: dict) -> tuple[dict, list[dict]]:
    s = hit["_source"]
    classe = s.get("classe") or {}
    movimentos = movimentos_de(s)
    fase, evidencia, data_transito = classificar_fase(classe.get("codigo"), movimentos,
                                                      regras_cumprimento)
    processo = {
        "id": hit["_id"],
        "numero": s.get("numeroProcesso"),
        "grau": s.get("grau"),
        "classe_codigo": classe.get("codigo"),
        "classe_nome": classe.get("nome"),
        "sistema": (s.get("sistema") or {}).get("nome"),
        "formato": (s.get("formato") or {}).get("nome"),
        "orgao": (s.get("orgaoJulgador") or {}).get("nome"),
        "data_ajuizamento": s.get("dataAjuizamento"),
        "ultima_atualizacao": s.get("dataHoraUltimaAtualizacao"),
        "assuntos": json.dumps(
            [{"codigo": a.get("codigo"), "nome": a.get("nome")} for a in _achatar(s.get("assuntos"))],
            ensure_ascii=False,
        ),
        "fase": fase,
        "evidencia": evidencia,
        "data_transito": data_transito,
        "bruto": json.dumps(s, ensure_ascii=False),
    }
    return processo, movimentos
