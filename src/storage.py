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

    def close(self) -> None:
        self._conn.close()
