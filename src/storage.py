"""
Armazenamento dos avistamentos em SQLite.

SQLite porque é zero-setup (um arquivo só, sem servidor de banco pra instalar) e
mais que suficiente para o volume de dados desse projeto (alguns avistamentos por
dia, no máximo). Isso também já deixa o terreno pronto pra Fase 3 (dashboard web):
o site só vai ler dessa mesma tabela.

Duas formas de abrir o store:

    SightingsStore(DB_PATH)                   # pipeline: cria a tabela e grava
    SightingsStore(DB_PATH, readonly=True)    # dashboard: só lê, nunca escreve
"""

from __future__ import annotations

import pathlib
import sqlite3
from datetime import datetime, timezone

from .identify import Identification

SCHEMA = """
CREATE TABLE IF NOT EXISTS sightings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seen_at TEXT NOT NULL,
    species_common_name TEXT NOT NULL,
    species_scientific_name TEXT,
    confidence REAL NOT NULL,
    source TEXT NOT NULL,
    image_path TEXT NOT NULL
);
"""


class SightingsStore:
    def __init__(self, db_path: pathlib.Path, *, readonly: bool = False):
        self.db_path = db_path
        # O dashboard precisa saber se está olhando pro banco de verdade ou pro
        # banco vazio de emergência, pra poder avisar a família em vez de fingir
        # que simplesmente não apareceu nenhum pássaro.
        self.sem_banco = False

        if readonly:
            self._conn = self._abrir_somente_leitura(db_path)
            return

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: main.py grava avistamentos numa thread de
        # segundo plano (pra não travar a captura da câmera esperando rede),
        # separada da thread que abre a conexão.
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute(SCHEMA)
        self._conn.commit()

    def _abrir_somente_leitura(self, db_path: pathlib.Path) -> sqlite3.Connection:
        """
        Conexão que o próprio SQLite recusa usar pra escrever.

        É "somente leitura" como garantia, não como disciplina: com mode=ro nem
        um INSERT acidental no código do dashboard consegue sujar o banco da
        família, e o CREATE TABLE do construtor normal (que é uma escrita, e que
        criaria um arquivo de banco vazio só por alguém ter aberto a página) não
        roda. as_uri() resolve o acento do caminho ("Área de Trabalho") que o
        SQLite não aceitaria cru numa URI.

        Se o arquivo não existe, está ilegível ou ainda não tem a tabela (clone
        novo, pipeline nunca rodou), cai pra um banco vazio em memória: a página
        abre, todas as consultas devolvem vazio e a família vê um aviso em vez
        de um erro 500.
        """
        conn = None
        try:
            conn = sqlite3.connect(
                f"{db_path.as_uri()}?mode=ro",
                uri=True,
                # Se o pipeline estiver no meio de um INSERT, espera um pouco em
                # vez de devolver "database is locked" na cara de quem abriu.
                timeout=2.0,
                check_same_thread=False,
            )
            conn.execute("SELECT 1 FROM sightings LIMIT 1").fetchone()
        except (sqlite3.Error, ValueError, OSError):
            if conn is not None:
                conn.close()
            conn = sqlite3.connect(":memory:", check_same_thread=False)
            conn.execute(SCHEMA)
            self.sem_banco = True
        conn.row_factory = sqlite3.Row
        return conn

    def add(self, identification: Identification, image_path: pathlib.Path) -> int:
        cur = self._conn.execute(
            """
            INSERT INTO sightings
                (seen_at, species_common_name, species_scientific_name, confidence, source, image_path)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                identification.species_common_name,
                identification.species_scientific_name,
                identification.confidence,
                identification.source,
                str(image_path),
            ),
        )
        self._conn.commit()
        return cur.lastrowid

    def count_sightings_of(self, species_common_name: str) -> int:
        cur = self._conn.execute(
            "SELECT COUNT(*) FROM sightings WHERE species_common_name = ?",
            (species_common_name,),
        )
        return cur.fetchone()[0]

    def all_species_count(self) -> int:
        cur = self._conn.execute(
            "SELECT COUNT(DISTINCT species_common_name) FROM sightings"
        )
        return cur.fetchone()[0]

    def recent(self, limit: int = 20) -> list[sqlite3.Row]:
        self._conn.row_factory = sqlite3.Row
        cur = self._conn.execute(
            "SELECT * FROM sightings ORDER BY seen_at DESC LIMIT ?", (limit,)
        )
        return cur.fetchall()

    # Nas consultas do dashboard abaixo, "seen_at" é sempre convertido com o
    # modificador 'localtime' do SQLite antes de virar data. O banco guarda UTC
    # (veja add()), mas quem olha o dashboard pensa em horário de Brasília: sem
    # isso, tudo que acontece depois das 21h local era contado no dia seguinte
    # do gráfico, e o filtro "Hoje" (que já vem em data local) pegava o
    # intervalo errado. O parâmetro de data NÃO leva 'localtime' — ele já chega
    # como data local pura ("2026-09-30"), e converter de novo jogaria o
    # intervalo um dia pra trás.

    @staticmethod
    def _filtro_especie(especie: str | None) -> tuple[str, tuple]:
        """
        Pedaço opcional do WHERE pro filtro por espécie.

        O trecho de SQL é literal, escrito aqui; o nome da espécie (que vem da
        query string) continua indo como parâmetro ?, então interpolar isso numa
        f-string não abre caminho pra injeção.
        """
        if especie:
            return " AND species_common_name = ?", (especie,)
        return "", ()

    _JANELA = """
        WHERE date(seen_at, 'localtime') >= date(?)
          AND date(seen_at, 'localtime') <= date(?)
    """

    def sightings_in_range(
        self,
        start_iso: str,
        end_iso: str,
        especie: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict]:
        """
        Avistamentos entre duas datas (inclusivo), mais recentes primeiro.

        'limit' existe porque o atalho "Tudo" renderizava uma <img> por linha do
        banco: com um ano de uso isso vira uma página de milhares de fotos que o
        celular não aguenta. O dashboard pagina.
        """
        self._conn.row_factory = sqlite3.Row
        extra, params = self._filtro_especie(especie)
        sql = f"SELECT * FROM sightings {self._JANELA} {extra} ORDER BY seen_at DESC"
        args: list = [start_iso, end_iso, *params]
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            args += [int(limit), int(offset)]
        cur = self._conn.execute(sql, args)
        return [dict(row) for row in cur.fetchall()]

    def count_in_range(
        self, start_iso: str, end_iso: str, especie: str | None = None
    ) -> int:
        """Quantos avistamentos existem no período — o total antes da paginação."""
        extra, params = self._filtro_especie(especie)
        cur = self._conn.execute(
            f"SELECT COUNT(*) FROM sightings {self._JANELA} {extra}",
            [start_iso, end_iso, *params],
        )
        return cur.fetchone()[0]

    def counts_by_day(
        self, start_iso: str, end_iso: str, especie: str | None = None
    ) -> list[tuple[str, int]]:
        """Quantidade de avistamentos por dia no período. Usado pelo dashboard."""
        extra, params = self._filtro_especie(especie)
        cur = self._conn.execute(
            f"""
            SELECT date(seen_at, 'localtime') AS day, COUNT(*) AS n
            FROM sightings {self._JANELA} {extra}
            GROUP BY day
            ORDER BY day ASC
            """,
            [start_iso, end_iso, *params],
        )
        return [(row[0], row[1]) for row in cur.fetchall()]

    def counts_by_hour(
        self, start_iso: str, end_iso: str, especie: str | None = None
    ) -> list[tuple[str, int]]:
        """
        Quantidade de avistamentos por hora do dia (00..23), no período.

        A hora já estava no banco e não aparecia em lugar nenhum — e "a que
        horas eles vêm" é exatamente o tipo de coisa que a família comenta.
        """
        extra, params = self._filtro_especie(especie)
        cur = self._conn.execute(
            f"""
            SELECT strftime('%H', seen_at, 'localtime') AS hora, COUNT(*) AS n
            FROM sightings {self._JANELA} {extra}
            GROUP BY hora
            ORDER BY hora ASC
            """,
            [start_iso, end_iso, *params],
        )
        return [(row[0], row[1]) for row in cur.fetchall()]

    def counts_by_species(self, start_iso: str, end_iso: str) -> list[tuple[str, int]]:
        """
        Quantidade por espécie no período, do mais pro menos visto.

        Nunca leva filtro de espécie: essa lista é o menu de filtros da página,
        então precisa continuar mostrando todas as outras pra dar pra trocar.
        """
        cur = self._conn.execute(
            f"""
            SELECT species_common_name, COUNT(*) AS n
            FROM sightings {self._JANELA}
            GROUP BY species_common_name
            ORDER BY n DESC, species_common_name ASC
            """,
            (start_iso, end_iso),
        )
        return [(row[0], row[1]) for row in cur.fetchall()]

    def first_sighting_per_species(self) -> list[dict]:
        """
        O primeiro avistamento de cada espécie em toda a história do banco.

        Serve pro selo "1ª vez!" — a mesma informação que a notificação do
        Telegram já dá na hora e que a página não dava. Ignora o período de
        propósito: "primeira vez" é primeira vez de verdade, não primeira vez
        dentro do filtro que está aberto.

        O id vem de carona no MIN(seen_at): com um único agregado min() na
        consulta, o SQLite garante que as colunas soltas são as da linha que
        ganhou o mínimo. Não dá pra usar MIN(id) no lugar porque o id é ordem de
        inserção, e o seed (e qualquer reprocessamento) grava fora de ordem.
        """
        cur = self._conn.execute(
            """
            SELECT id, species_common_name, MIN(seen_at) AS seen_at
            FROM sightings
            GROUP BY species_common_name
            """
        )
        return [
            {"id": row[0], "species_common_name": row[1], "seen_at": row[2]}
            for row in cur.fetchall()
        ]

    def latest_sighting(self) -> dict | None:
        """
        O avistamento mais recente do banco inteiro, ignorando filtro.

        É o que responde "a câmera ainda está viva?" no topo da página — por
        isso não pode depender do período que a pessoa escolheu.
        """
        self._conn.row_factory = sqlite3.Row
        cur = self._conn.execute("SELECT * FROM sightings ORDER BY seen_at DESC LIMIT 1")
        row = cur.fetchone()
        return dict(row) if row else None

    def earliest_sighting_date(self) -> str | None:
        """Data (YYYY-MM-DD) do avistamento mais antigo, ou None se o banco estiver vazio. Usado pelo dashboard."""
        cur = self._conn.execute("SELECT MIN(date(seen_at, 'localtime')) FROM sightings")
        return cur.fetchone()[0]

    def close(self) -> None:
        self._conn.close()
