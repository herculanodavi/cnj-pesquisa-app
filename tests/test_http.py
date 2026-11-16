import httpx
import pytest

from cnj.http import Cliente, Limites, LimiteExcedido
from cnj.store import Store


class Relogio:
    def __init__(self):
        self.t = 0.0
        self.dormidas = []

    def agora(self):
        return self.t

    def dormir(self, s):
        self.dormidas.append(s)
        self.t += s


def cliente(tmp_path, respostas, **lim):
    chamadas = []

    def handler(request):
        chamadas.append(request)
        return respostas.pop(0)

    relogio = Relogio()
    limites = Limites(intervalos={"api.test": 1.0}, jitter=0.0, backoff_base=2.0,
                      backoff_max=300.0, max_tentativas=lim.get("max_tentativas", 5))
    c = Cliente(Store(tmp_path / "t.sqlite"), limites, transport=httpx.MockTransport(handler),
                dormir=relogio.dormir, relogio=relogio.agora)
    return c, chamadas, relogio


def test_backoff_respeita_retry_after_e_depois_cacheia(tmp_path):
    c, chamadas, relogio = cliente(tmp_path, [
        httpx.Response(429, headers={"Retry-After": "30"}),
        httpx.Response(503),
        httpx.Response(200, json={"ok": True}),
    ])
    r = c.post_json("https://api.test/_search", {"q": 1})
    assert r.json() == {"ok": True} and not r.do_cache
    assert len(chamadas) == 3
    assert relogio.dormidas[0] == pytest.approx(30)   # Retry-After > backoff de 2s
    assert relogio.dormidas[1] == pytest.approx(4)    # 2 * 2^1

    r2 = c.post_json("https://api.test/_search", {"q": 1})
    assert r2.do_cache and len(chamadas) == 3


def test_intervalo_minimo_entre_requisicoes(tmp_path):
    c, chamadas, relogio = cliente(tmp_path, [httpx.Response(200, json={})] * 3)
    for i in range(3):
        c.get(f"https://api.test/{i}")
    assert relogio.dormidas == [pytest.approx(1.0), pytest.approx(1.0)]


def test_desiste_apos_max_tentativas(tmp_path):
    c, chamadas, _ = cliente(tmp_path, [httpx.Response(429)] * 3, max_tentativas=3)
    with pytest.raises(LimiteExcedido):
        c.get("https://api.test/x")
    assert len(chamadas) == 3


def test_erro_nao_retentavel_nao_vai_para_cache(tmp_path):
    c, chamadas, _ = cliente(tmp_path, [httpx.Response(404), httpx.Response(404)])
    assert c.get("https://api.test/x").status == 404
    assert c.get("https://api.test/x").status == 404
    assert len(chamadas) == 2


def test_429_desacelera_o_host_e_recupera_aos_poucos(tmp_path):
    c, chamadas, relogio = cliente(tmp_path, [
        httpx.Response(429),
        httpx.Response(200, json={}),
        httpx.Response(200, json={}),
    ])
    c.get("https://api.test/a")
    # 1s -> 2s após o 429; o sucesso seguinte reduz só 5%.
    assert c.intervalo_atual("api.test") == pytest.approx(1.9)
    relogio.dormidas.clear()
    c.get("https://api.test/b")
    assert relogio.dormidas == [pytest.approx(1.9)]
    assert c.intervalo_atual("api.test") == pytest.approx(1.805)
