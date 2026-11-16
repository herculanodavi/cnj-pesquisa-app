from cnj.esaj import parse_show, url_busca

HTML = """
<span id="classeProcesso">Ação Civil Pública</span>
<span id="assuntoProcesso">Área de Preservação Permanente</span>
<span id="varaProcesso">7ª Vara da Fazenda Pública</span>
<span id="labelSituacaoProcesso">Remetido a outro foro</span>
<table id="tableTodasPartes">
  <tr><td><span class="tipoDeParticipacao">Autor&nbsp;</span></td>
      <td class="nomeParteEAdvogado"> Ministério Público do Estado do Ceará </td></tr>
</table>
<table><tbody id="tabelaTodasMovimentacoes">
  <tr class="containerMovimentacao">
    <td class="dataMovimentacao">23/10/2022</td><td></td>
    <td class="descricaoMovimentacao">Migração SAJ PJe <br><span style="font-style: italic;">Migrado para o PJe</span></td>
  </tr>
  <tr class="containerMovimentacao">
    <td class="dataMovimentacao">29/08/2014</td><td></td>
    <td class="descricaoMovimentacao">Transitado em Julgado</td>
  </tr>
</tbody></table>
"""


def test_parse_show():
    d = parse_show(HTML, "u")
    assert d["classe"] == "Ação Civil Pública"
    assert d["migrado_pje"] is True
    assert d["partes"] == [{"tipo": "Autor", "nome": "Ministério Público do Estado do Ceará"}]
    assert d["movimentos"][0] == {"data": "2022-10-23", "codigo": None,
                                  "nome": "Migração SAJ PJe", "texto": "Migrado para o PJe"}
    assert d["movimentos"][1]["texto"] is None


def test_url_busca():
    u = url_busca("01400034320128060001")
    assert "numeroDigitoAnoUnificado=0140003-43.2012" in u and "foroNumeroUnificado=0001" in u
