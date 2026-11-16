# cnj: ACPs ambientais em cumprimento de sentença (TJCE)

Encontra processos do TJCE sobre APP / ocupação irregular em fase de cumprimento de sentença,
usando a API Pública do DataJud (metadados) e a consulta pública do e-SAJ (partes e texto das
movimentações). Gera uma planilha filtrável por palavra-chave.

## Configuração da chave

A chave pública do DataJud não é versionada. Copie `.env.example` para `.env` e preencha
`DATAJUD_API_KEY` com a chave vigente (https://datajud-wiki.cnj.jus.br/api-publica/acesso).

## Uso

```sh
uv sync
uv run cnj descobrir                 # confere classes/assuntos/movimentos reais dos exemplos
uv run cnj buscar                    # baixa do DataJud as ACPs com assuntos ambientais
uv run cnj buscar --numeros 0140003-43.2012.8.06.0001   # inclui processos específicos
uv run cnj enriquecer                # e-SAJ público dos que estão em cumprimento (--todas p/ todos)
uv run cnj exportar --palavras "itaoca,ocupação irregular" --saida data/itaoca.xlsx
```

Todos os comandos podem ser interrompidos e rodados de novo: respostas ficam em cache em
`data/cnj.sqlite`, a paginação do DataJud retoma do último cursor e o `enriquecer` pula os
processos já consultados (`--refresh` força de novo).

## Recorte (`[filtro]` no config.toml)

O `exportar` mantém só processos com foro de origem `0001` (Comarca de Fortaleza), com
Ministério Público ou Defensoria Pública no polo ativo (partes do e-SAJ) e trânsito em julgado
a partir de 2015 (ou, sem trânsito registrado, a primeira evidência de cumprimento). Ele também
mostra quantos processos ficaram de fora e por quê. Processos sem partes no e-SAJ (nativos do PJe)
só entram com `--sem-partes`; `--sem-filtro` desliga o recorte.

## Limites de uso

- Uma requisição por vez por host; ≥3 s entre chamadas ao DataJud e ≥10 s aos portais do TJCE
  (`[limites]` em `config.toml`).
- Freio adaptativo: cada 429/5xx dobra o intervalo daquele host (até 2 min) e ele só volta ao
  mínimo devagar (−5% a cada resposta bem-sucedida), em vez de retomar o ritmo cheio.
- Em 429/5xx/erro de rede: backoff exponencial (2 s, 4 s, … até 5 min), respeitando
  `Retry-After`; após `max_tentativas` o comando para e salva o progresso.
- A API Pública é para uso não comercial (Termo de Uso do CNJ). A chave pública fica em `.env`
  (`DATAJUD_API_KEY`, fora do git) e deve ser atualizada quando o CNJ trocá-la.

## Como a fase é decidida

Um processo é marcado como `cumprimento` se: a classe é de cumprimento (156, 12078), **ou**
existe movimento de trânsito em julgado (código 848 no DataJud, ou "Transitado em julgado" no
e-SAJ), **ou** um movimento menciona "cumprimento de sentença"/"evolução da classe". A coluna
`evidencia` mostra o motivo. As regras ficam em `[cumprimento]` no `config.toml`.

## Limitações conhecidas

- O DataJud não tem texto livre: a palavra-chave só é buscada nos metadados e, depois do
  `enriquecer`, no texto das movimentações do e-SAJ.
- O filtro inicial é classe + assunto. Processos cadastrados com outra classe/assunto (ex.:
  0140003-43.2012, Riacho Itaoca, está como *Procedimento Comum Cível / Obrigação de Fazer*)
  não aparecem; use `buscar --numeros` ou amplie `[busca]` no config.
- Processos migrados para o PJe (out/2022 em diante) têm no e-SAJ só o histórico até a
  migração; o restante vem do DataJud.
- PDFs ainda não são baixados (Etapa 2).

## Licença e termos de uso dos dados

- **Código:** [BSD Zero Clause (0BSD)](LICENSE), equivalente a domínio público: uso, cópia,
  modificação e distribuição livres, sem exigência de atribuição. As dependências (httpx,
  selectolax, openpyxl) usam licenças BSD/MIT e não são redistribuídas aqui.
- **Dados não são cobertos por essa licença.** O banco `data/cnj.sqlite` e as planilhas contêm
  informações obtidas do DataJud e do e-SAJ, que continuam sujeitas às regras de origem. O
  [Termo de Uso da API Pública do DataJud](https://datajud-wiki.cnj.jus.br/api-publica/termo-uso)
  (v1.2) admite apenas fins legais e **não comerciais**, veda distribuir ou explorar
  comercialmente informação derivada da API (item 3.8) e pede que o CNJ seja informado de estudos
  publicados a partir dela (item 3.9). Quem reutilizar os dados deve observar esses termos e a LGPD.
