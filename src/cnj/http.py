"""Cliente HTTP educado: uma requisição por vez por host, intervalo mínimo, backoff e cache."""

import hashlib
import json
import logging
import random
import ssl
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx

from .store import Store

log = logging.getLogger(__name__)

USER_AGENT = "cnj-pesquisa-acp/0.1 (pesquisa academica; uso nao comercial)"
RETENTAVEIS = {429, 500, 502, 503, 504}


class LimiteExcedido(Exception):
    """O servidor continuou recusando depois de todas as tentativas."""


@dataclass
class Resposta:
    status: int
    corpo: bytes
    url_final: str
    do_cache: bool

    def json(self):
        return json.loads(self.corpo)

    @property
    def texto(self):
        return self.corpo.decode("utf-8", errors="replace")


@dataclass
class Limites:
    intervalos: dict[str, float]      # host -> intervalo mínimo em segundos
    intervalo_padrao: float = 3.0
    jitter: float = 0.5
    backoff_base: float = 2.0
    backoff_max: float = 300.0
    max_tentativas: int = 8
    timeout: float = 60.0
    # Freio adaptativo: cada 429/5xx multiplica o intervalo do host por `fator_lentidao`
    # (até `intervalo_max`); cada sucesso o reduz por `recuperacao`, sem passar do mínimo.
    fator_lentidao: float = 2.0
    recuperacao: float = 0.95
    intervalo_max: float = 120.0
    tls_legado: tuple[str, ...] = ()  # hosts que só negociam cifras antigas (ex.: e-SAJ TJCE)


def _retry_after(valor: str | None) -> float | None:
    if not valor:
        return None
    try:
        return max(0.0, float(valor))
    except ValueError:
        pass
    try:
        return max(0.0, parsedate_to_datetime(valor).timestamp() - time.time())
    except (TypeError, ValueError):
        return None


class Cliente:
    def __init__(self, store: Store, limites: Limites, transport=None, dormir=time.sleep,
                 relogio=time.monotonic):
        self.store = store
        self.limites = limites
        self.dormir = dormir
        self.relogio = relogio
        self._ultimo: dict[str, float] = {}
        self._intervalo: dict[str, float] = {}  # intervalo atual (adaptativo) por host
        mounts = None
        if transport is None and limites.tls_legado:
            # Mantém a verificação do certificado; só aceita cifras de segurança nível 1.
            ctx = ssl.create_default_context()
            ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
            mounts = {f"https://{h}": httpx.HTTPTransport(verify=ctx) for h in limites.tls_legado}
        self.http = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=limites.timeout,
            follow_redirects=True,
            transport=transport,
            mounts=mounts,
        )

    def close(self):
        self.http.close()

    def _intervalo_minimo(self, host: str) -> float:
        return self.limites.intervalos.get(host, self.limites.intervalo_padrao)

    def intervalo_atual(self, host: str) -> float:
        return self._intervalo.get(host, self._intervalo_minimo(host))

    def _desacelerar(self, host: str):
        antes = self.intervalo_atual(host)
        depois = min(self.limites.intervalo_max, max(antes, 0.5) * self.limites.fator_lentidao)
        self._intervalo[host] = depois
        if depois > antes:
            log.warning("%s: reduzindo o ritmo; intervalo entre requisições %.1fs -> %.1fs",
                        host, antes, depois)

    def _acelerar(self, host: str):
        self._intervalo[host] = max(self._intervalo_minimo(host),
                                    self.intervalo_atual(host) * self.limites.recuperacao)

    def _esperar_vez(self, host: str):
        intervalo = self.intervalo_atual(host)
        ultimo = self._ultimo.get(host)
        if ultimo is not None:
            falta = intervalo + random.uniform(0, self.limites.jitter) - (self.relogio() - ultimo)
            if falta > 0:
                self.dormir(falta)
        self._ultimo[host] = self.relogio()

    def request(self, metodo: str, url: str, *, json_body=None, headers=None,
                usar_cache: bool = True) -> Resposta:
        corpo_req = json.dumps(json_body, sort_keys=True) if json_body is not None else ""
        chave = hashlib.sha256(f"{metodo} {url}\n{corpo_req}".encode()).hexdigest()
        if usar_cache and (hit := self.store.cache_get(chave)):
            return Resposta(hit["status"], hit["corpo"], hit["url_final"], True)

        host = urlsplit(url).hostname or ""
        for tentativa in range(self.limites.max_tentativas):
            self._esperar_vez(host)
            espera_servidor = None
            try:
                r = self.http.request(metodo, url, json=json_body, headers=headers)
            except httpx.TransportError as e:
                log.warning("%s %s: erro de rede (%s)", metodo, url, e)
                status = None
            else:
                status = r.status_code
                if status not in RETENTAVEIS:
                    self._acelerar(host)
                    if 200 <= status < 300:
                        self.store.cache_put(chave, url, str(r.url), status, r.content)
                    return Resposta(status, r.content, str(r.url), False)
                espera_servidor = _retry_after(r.headers.get("Retry-After"))
            self._desacelerar(host)

            if tentativa + 1 == self.limites.max_tentativas:
                break
            espera = min(self.limites.backoff_max, self.limites.backoff_base * 2 ** tentativa)
            if espera_servidor is not None:
                espera = max(espera, espera_servidor)
            espera += random.uniform(0, self.limites.jitter)
            log.warning("%s %s -> %s; tentativa %d/%d, aguardando %.0fs",
                        metodo, host, status or "erro", tentativa + 1,
                        self.limites.max_tentativas, espera)
            self.dormir(espera)

        raise LimiteExcedido(f"{metodo} {url}: desisti após {self.limites.max_tentativas} tentativas")

    def get(self, url: str, **kw) -> Resposta:
        return self.request("GET", url, **kw)

    def post_json(self, url: str, body, **kw) -> Resposta:
        return self.request("POST", url, json_body=body, **kw)
