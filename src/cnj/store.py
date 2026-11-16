"""Persistência em SQLite: cache HTTP, processos, movimentações, dados do e-SAJ e cursores."""

import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS http_cache (
    chave TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    url_final TEXT,
    status INTEGER NOT NULL,
    corpo BLOB NOT NULL,
    obtido_em REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS processos (
    id TEXT PRIMARY KEY,              -- _id do DataJud (ex.: TJCE_G1_...)
    numero TEXT NOT NULL,             -- 20 dígitos
    grau TEXT,
    classe_codigo INTEGER,
    classe_nome TEXT,
    sistema TEXT,
    formato TEXT,
    orgao TEXT,
    data_ajuizamento TEXT,
    ultima_atualizacao TEXT,
    assuntos TEXT,                    -- JSON [{codigo, nome}]
    fase TEXT,                        -- 'cumprimento' | 'conhecimento'
    evidencia TEXT,
    data_transito TEXT,
    bruto TEXT NOT NULL               -- _source completo
);
CREATE INDEX IF NOT EXISTS processos_numero ON processos(numero);
CREATE TABLE IF NOT EXISTS movimentos (
    numero TEXT NOT NULL,
    fonte TEXT NOT NULL,              -- 'datajud' | 'esaj'
    data TEXT,
    codigo INTEGER,
    nome TEXT,
    texto TEXT
);
CREATE INDEX IF NOT EXISTS movimentos_numero ON movimentos(numero, fonte);
CREATE TABLE IF NOT EXISTS esaj (
    numero TEXT PRIMARY KEY,
    encontrado INTEGER NOT NULL,
    url TEXT,
    classe TEXT,
    assunto TEXT,
    vara TEXT,
    juiz TEXT,
    situacao TEXT,
    migrado_pje INTEGER,
    partes TEXT,                      -- JSON [{tipo, nome}]
    obtido_em REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS cursores (
    nome TEXT PRIMARY KEY,
    valor TEXT
);
"""


class Store:
    def __init__(self, caminho: str | Path):
        Path(caminho).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(caminho)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    # cache HTTP -----------------------------------------------------------
    def cache_get(self, chave: str):
        return self.db.execute("SELECT * FROM http_cache WHERE chave = ?", (chave,)).fetchone()

    def cache_put(self, chave: str, url: str, url_final: str, status: int, corpo: bytes):
        self.db.execute(
            "INSERT OR REPLACE INTO http_cache VALUES (?, ?, ?, ?, ?, ?)",
            (chave, url, url_final, status, corpo, time.time()),
        )
        self.db.commit()

    # cursores -------------------------------------------------------------
    def cursor_get(self, nome: str):
        row = self.db.execute("SELECT valor FROM cursores WHERE nome = ?", (nome,)).fetchone()
        return json.loads(row["valor"]) if row else None

    def cursor_put(self, nome: str, valor):
        self.db.execute(
            "INSERT OR REPLACE INTO cursores VALUES (?, ?)", (nome, json.dumps(valor))
        )
        self.db.commit()

    def cursor_del(self, nome: str):
        self.db.execute("DELETE FROM cursores WHERE nome = ?", (nome,))
        self.db.commit()

    # processos ------------------------------------------------------------
    def salvar_processo(self, p: dict, movimentos: list[dict]):
        self.db.execute(
            """INSERT OR REPLACE INTO processos VALUES
               (:id, :numero, :grau, :classe_codigo, :classe_nome, :sistema, :formato, :orgao,
                :data_ajuizamento, :ultima_atualizacao, :assuntos, :fase, :evidencia,
                :data_transito, :bruto)""",
            p,
        )
        self.substituir_movimentos(p["numero"], "datajud", movimentos, commit=False)
        self.db.commit()

    def substituir_movimentos(self, numero: str, fonte: str, movimentos: list[dict], commit=True):
        self.db.execute("DELETE FROM movimentos WHERE numero = ? AND fonte = ?", (numero, fonte))
        self.db.executemany(
            "INSERT INTO movimentos VALUES (?, ?, ?, ?, ?, ?)",
            [
                (numero, fonte, m.get("data"), m.get("codigo"), m.get("nome"), m.get("texto"))
                for m in movimentos
            ],
        )
        if commit:
            self.db.commit()

    def movimentos(self, numero: str, fonte: str | None = None):
        if fonte:
            sql, args = "SELECT * FROM movimentos WHERE numero = ? AND fonte = ?", (numero, fonte)
        else:
            sql, args = "SELECT * FROM movimentos WHERE numero = ?", (numero,)
        return [dict(r) for r in self.db.execute(sql, args)]

    def processos(self, somente_cumprimento: bool = False):
        sql = "SELECT * FROM processos"
        if somente_cumprimento:
            sql += " WHERE fase = 'cumprimento'"
        return [dict(r) for r in self.db.execute(sql + " ORDER BY numero, grau")]

    def atualizar_fase(self, id_: str, fase: str, evidencia: str, data_transito: str | None):
        self.db.execute(
            "UPDATE processos SET fase = ?, evidencia = ?, data_transito = ? WHERE id = ?",
            (fase, evidencia, data_transito, id_),
        )
        self.db.commit()

    # e-SAJ ----------------------------------------------------------------
    def salvar_esaj(self, numero: str, dados: dict | None):
        if dados is None:
            self.db.execute(
                "INSERT OR REPLACE INTO esaj (numero, encontrado, obtido_em) VALUES (?, 0, ?)",
                (numero, time.time()),
            )
        else:
            self.db.execute(
                "INSERT OR REPLACE INTO esaj VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    numero,
                    dados["url"],
                    dados.get("classe"),
                    dados.get("assunto"),
                    dados.get("vara"),
                    dados.get("juiz"),
                    dados.get("situacao"),
                    int(dados.get("migrado_pje", False)),
                    json.dumps(dados.get("partes", []), ensure_ascii=False),
                    time.time(),
                ),
            )
            self.substituir_movimentos(numero, "esaj", dados.get("movimentos", []), commit=False)
        self.db.commit()

    def esaj(self, numero: str):
        row = self.db.execute("SELECT * FROM esaj WHERE numero = ?", (numero,)).fetchone()
        return dict(row) if row else None
