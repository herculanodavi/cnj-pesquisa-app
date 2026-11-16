import json

from cnj.filtros import buscar_palavras, classificar_fase, formatar_cnj

REGRAS = {
    "classes": [156, 12078],
    "movimentos_codigos": [848],
    "movimentos_trechos": ["transito em julgado", "transitado em julgado", "cumprimento de sentenca"],
}


def test_formatar_cnj():
    assert formatar_cnj("01740165820188060001") == "0174016-58.2018.8.06.0001"


def test_conhecimento_sem_evidencia():
    assert classificar_fase(65, [{"data": "2020-01-01", "codigo": 51, "nome": "Conclusos"}], REGRAS) == (
        "conhecimento", "", None)


def test_transito_por_codigo_datajud():
    fase, evid, data = classificar_fase(65, [
        {"data": "2025-03-10T00:00:00", "codigo": 848, "nome": "Trânsito em julgado"}], REGRAS)
    assert fase == "cumprimento" and data == "2025-03-10" and "848" in evid


def test_transito_por_texto_esaj():
    fase, _, data = classificar_fase(7, [
        {"data": "2014-08-29", "codigo": None, "nome": "Transitado em Julgado", "texto": None}], REGRAS)
    assert fase == "cumprimento" and data == "2014-08-29"


def test_classe_de_cumprimento():
    assert classificar_fase(156, [], REGRAS)[0] == "cumprimento"


def test_palavras_ignoram_acento_e_caixa():
    processo = {"classe_nome": "Ação Civil Pública", "orgao": "7ª Vara",
                "assuntos": json.dumps([{"codigo": 1, "nome": "Área de Preservação Permanente"}])}
    movs = [{"fonte": "esaj", "nome": "Concedida a Medida Liminar",
             "texto": "retirada de ocupação irregular na APP do riacho Itaoca"}]
    achados = buscar_palavras(processo, movs, None, ["itaoca", "preservacao permanente", "mangue"])
    assert achados == {"itaoca": ["mov_esaj"], "preservacao permanente": ["assuntos"]}


def test_autor_mp_no_polo_ativo():
    from cnj.filtros import autores_aceitos
    filtro = {"autores_trechos": ["ministerio publico", "defensoria publica"],
              "polo_ativo_tipos": ["autor", "requerente"]}
    partes = [{"tipo": "Autor", "nome": "Município de Fortaleza"},
              {"tipo": "Promotor(a)", "nome": "Ministério Público do Estado do Ceará"}]
    assert autores_aceitos(partes, filtro) == []  # MP só como fiscal da lei
    partes.append({"tipo": "Requerente", "nome": "Defensoria Pública do Estado do Ceará"})
    assert autores_aceitos(partes, filtro) == ["Defensoria Pública do Estado do Ceará"]


def test_data_referencia_e_foro():
    from cnj.filtros import data_referencia, foro_origem
    assert foro_origem("0174016-58.2018.8.06.0001") == "0001"
    assert data_referencia({"data_transito": None,
                            "evidencia": "classe 12078; movimento x em 2025-11-24; y em 2025-05-06"}) == "2025-05-06"
