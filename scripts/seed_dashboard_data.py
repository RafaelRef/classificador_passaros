"""
Gera dados fake realistas em data/sightings.db + fotos placeholder em
data/captures/, só pra ter o que visualizar ao mexer no dashboard (src/dashboard.py)
num ambiente novo (clone fresco do repo), já que os dados reais não vão pro Git
de propósito.

NÃO usar isso na máquina com os dados reais sem querer — ele só adiciona linhas
novas, não apaga nada, mas os avistamentos fake vão aparecer misturados com os
de verdade no dashboard.

Rodar (na raiz do projeto, com o venv ativado):

    python -m scripts.seed_dashboard_data
"""

from __future__ import annotations

import pathlib
import random
from datetime import datetime, timedelta, timezone

import cv2
import numpy as np

from src.identify import Identification
from src.storage import SightingsStore

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "sightings.db"
CAPTURES_DIR = PROJECT_ROOT / "data" / "captures"

# (nome comum, nome científico) — espécies plausíveis de varanda no Brasil.
FAKE_SPECIES = [
    ("Bem-te-vi", "Pitangus sulphuratus"),
    ("Saíra-sete-cores", "Tangara seledon"),
    ("Pica-pau-do-campo", "Colaptes campestris"),
    ("Sabiá-laranjeira", "Turdus rufiventris"),
    ("Beija-flor-tesoura", "Eupetomena macroura"),
    ("Rolinha-roxa", "Columbina talpacoti"),
    ("João-de-barro", "Furnarius rufus"),
    ("Coruja-das-torres", "Tyto alba"),
]

COLORS_BGR = [
    (60, 120, 60), (40, 90, 170), (150, 110, 40), (90, 60, 140),
    (50, 150, 150), (130, 150, 40), (60, 60, 160), (140, 90, 90),
]


def _fake_image(path: pathlib.Path, seed_color) -> None:
    img = np.full((240, 320, 3), seed_color, dtype=np.uint8)
    noise = np.random.randint(0, 30, img.shape, dtype=np.uint8)
    img = cv2.add(img, noise)
    cv2.imwrite(str(path), img)


def main(n: int = 40) -> None:
    CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
    store = SightingsStore(DB_PATH)

    now = datetime.now(timezone.utc)
    created = 0
    for i in range(n):
        common, scientific = random.choice(FAKE_SPECIES)
        color = COLORS_BGR[FAKE_SPECIES.index((common, scientific)) % len(COLORS_BGR)]
        seen_at = now - timedelta(
            days=random.randint(0, 45),
            hours=random.randint(0, 23),
            minutes=random.randint(0, 59),
        )
        image_path = CAPTURES_DIR / f"fake_{i}_{int(seen_at.timestamp())}.jpg"
        _fake_image(image_path, color)

        identification = Identification(
            species_common_name=common,
            species_scientific_name=scientific,
            confidence=round(random.uniform(0.4, 0.98), 2),
            source="fake-seed",
        )
        # SightingsStore.add() usa datetime.now() internamente pro seen_at, então
        # inserimos direto pra poder espalhar as datas no passado.
        store._conn.execute(
            """
            INSERT INTO sightings
                (seen_at, species_common_name, species_scientific_name, confidence, source, image_path)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                seen_at.isoformat(),
                identification.species_common_name,
                identification.species_scientific_name,
                identification.confidence,
                identification.source,
                str(image_path),
            ),
        )
        created += 1

    store._conn.commit()
    store.close()
    print(f"[seed] {created} avistamentos fake criados em {DB_PATH}")
    print("[seed] rode 'python -m src.dashboard' e abra http://localhost:5050")


if __name__ == "__main__":
    main()
