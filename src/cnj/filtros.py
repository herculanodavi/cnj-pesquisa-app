"""Regra de fase (cumprimento de sentença) e busca de palavras-chave."""

import json
import re
import unicodedata


def normalizar(texto: str | None) -> str:
    """Minúsculas, sem acentos e com espaços colapsados, para comparar texto jurídico."""
    if not texto:
        return ""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", sem_acento).strip().lower()


def so_digitos(numero: str) -> str:
    return re.sub(r"\D", "", numero)


def formatar_cnj(numero: str) -> str:
    """'01740165820188060001' -> '0174016-58.2018.8.06.0001'."""
    n = so_digitos(numero)
    if len(n) != 20:
        return numero
    return f"{n[:7]}-{n[7:9]}.{n[9:13]}.{n[13]}.{n[14:16]}.{n[16:]}"


def classificar_fase(classe_codigo: int | None, movimentos: list[dict], regras: dict):
    """Decide se o processo está em cumprimento de sentença.

    `movimentos` vem de qualquer fonte, com chaves data/codigo/nome/texto.
    Retorna (fase, evidencia, data_transito).
    """
    classes = set(regras.get("classes", []))
    codigos = set(regras.get("movimentos_codigos", []))
    trechos = [normalizar(t) for t in regras.get("movimentos_trechos", [])]

    data_transito = None
    evidencias = []
    if classe_codigo in classes:
        evidencias.append(f"classe {classe_codigo}")

    for m in sorted(movimentos, key=lambda m: m.get("data") or ""):
        nome_texto = normalizar(f"{m.get('nome') or ''} {m.get('texto') or ''}")
        motivo = None
        if m.get("codigo") in codigos:
            motivo = f"movimento {m['codigo']}"
        elif trecho := next((t for t in trechos if t in nome_texto), None):
            motivo = f"movimento '{trecho}'"
        if motivo:
            data = (m.get("data") or "")[:10]
            evidencias.append(f"{motivo} em {data}" if data else motivo)
            if "julgado" in (motivo + nome_texto) and data_transito is None:
                data_transito = data or None

    if evidencias:
        return "cumprimento", "; ".join(dict.fromkeys(evidencias)), data_transito
    return "conhecimento", "", None


def buscar_palavras(processo: dict, movimentos: list[dict], esaj: dict | None,
                    palavras: list[str]) -> dict[str, list[str]]:
    """Retorna {palavra: [onde apareceu]} para as palavras encontradas."""
    campos = {
        "classe": processo.get("classe_nome"),
        "assuntos": " ".join(a.get("nome", "") for a in json.loads(processo.get("assuntos") or "[]")),
        "orgao": processo.get("orgao"),
    }
    if esaj and esaj.get("encontrado"):
        campos["esaj_assunto"] = esaj.get("assunto")
        campos["partes"] = " ".join(p["nome"] for p in json.loads(esaj.get("partes") or "[]"))
    for m in movimentos:
        chave = f"mov_{m['fonte']}"
        campos[chave] = f"{campos.get(chave, '')} {m.get('nome') or ''} {m.get('texto') or ''}"

    normalizados = {k: normalizar(v) for k, v in campos.items()}
    achados = {}
    for palavra in palavras:
        p = normalizar(palavra)
        if not p:
            continue
        onde = [k for k, v in normalizados.items() if p in v]
        if onde:
            achados[palavra] = onde
    return achados


def foro_origem(numero: str) -> str:
    """Últimos 4 dígitos do número CNJ (OOOO): foro de origem. No TJCE, 0001 = Fortaleza."""
    return so_digitos(numero)[-4:]


def data_referencia(processo: dict) -> str | None:
    """Data do trânsito em julgado; sem ela, a primeira evidência datada de cumprimento."""
    if processo.get("data_transito"):
        return processo["data_transito"]
    datas = re.findall(r"\d{4}-\d{2}-\d{2}", processo.get("evidencia") or "")
    return min(datas) if datas else None


def autores_aceitos(partes: list[dict], filtro: dict) -> list[str]:
    """Partes do polo ativo cujo nome contém algum dos trechos (ex.: Ministério Público)."""
    tipos = {normalizar(t) for t in filtro.get("polo_ativo_tipos", [])}
    trechos = [normalizar(t) for t in filtro.get("autores_trechos", [])]
    return [
        p["nome"] for p in partes
        if normalizar(p["tipo"]).replace("(a)", "").strip() in tipos
        and any(t in normalizar(p["nome"]) for t in trechos)
    ]
