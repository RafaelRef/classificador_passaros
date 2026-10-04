"""
Armazenamento dos avistamentos em SQLite.

SQLite porque é zero-setup (um arquivo só, sem servidor de banco pra instalar) e
mais que suficiente para o volume de dados desse projeto (alguns avistamentos por
dia, no máximo). Isso também já deixa o terreno pronto pra Fase 3 (dashboard web):
o site só vai ler dessa mesma tabela.
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
    def __init__(self, db_path: pathlib.Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: main.py grava avistamentos numa thread de
        # segundo plano (pra não travar a captura da câmera esperando rede),
        # separada da thread que abre a conexão.
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute(SCHEMA)
        self._conn.commit()

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

    def sightings_in_range(self, start_iso: str, end_iso: str) -> list[dict]:
        """Avistamentos entre duas datas (inclusivo), mais recentes primeiro. Usado pelo dashboard."""
        self._conn.row_factory = sqlite3.Row
        cur = self._conn.execute(
            """
            SELECT * FROM sightings
            WHERE date(seen_at, 'localtime') >= date(?)
              AND date(seen_at, 'localtime') <= date(?)
            ORDER BY seen_at DESC
            """,
            (start_iso, end_iso),
        )
        return [dict(row) for row in cur.fetchall()]

    def counts_by_day(self, start_iso: str, end_iso: str) -> list[tuple[str, int]]:
        """Quantidade de avistamentos por dia no período. Usado pelo dashboard."""
        cur = self._conn.execute(
            """
            SELECT date(seen_at, 'localtime') AS day, COUNT(*) AS n
            FROM sightings
            WHERE date(seen_at, 'localtime') >= date(?)
              AND date(seen_at, 'localtime') <= date(?)
            GROUP BY day
            ORDER BY day ASC
            """,
            (start_iso, end_iso),
        )
        return [(row[0], row[1]) for row in cur.fetchall()]

    def counts_by_species(self, start_iso: str, end_iso: str) -> list[tuple[str, int]]:
        """Quantidade de avistamentos por espécie no período, do mais pro menos visto. Usado pelo dashboard."""
        cur = self._conn.execute(
            """
            SELECT species_common_name, COUNT(*) AS n
            FROM sightings
            WHERE date(seen_at, 'localtime') >= date(?)
              AND date(seen_at, 'localtime') <= date(?)
            GROUP BY species_common_name
            ORDER BY n DESC
            """,
            (start_iso, end_iso),
        )
        return [(row[0], row[1]) for row in cur.fetchall()]

    def earliest_sighting_date(self) -> str | None:
        """Data (YYYY-MM-DD) do avistamento mais antigo, ou None se o banco estiver vazio. Usado pelo dashboard."""
        cur = self._conn.execute("SELECT MIN(date(seen_at, 'localtime')) FROM sightings")
        return cur.fetchone()[0]

    def close(self) -> None:
        self._conn.close()
