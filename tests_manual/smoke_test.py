"""
Smoke test manual: não usa webcam nem o detector YOLO de verdade (precisaria
de uma foto real de bicho pra detectar algo — não faz sentido testar isso com
frame sintético). Em vez disso, simula "como se o AnimalDetector tivesse
achado um bicho" e exercita o resto do pipeline (identify.py, storage.py,
notify.py) de ponta a ponta.

Rodar: python -m tests_manual.smoke_test
"""

import pathlib
import shutil
import sys

import cv2
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.identify import build_identifier
from src.notify import build_notifier
from src.storage import SightingsStore

TMP_DIR = pathlib.Path(__file__).resolve().parent / "_tmp"


def make_frame() -> np.ndarray:
    """Frame sintético em resolução realista (tipo webcam 480p)."""
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    frame[:, :] = (20, 20, 20)  # fundo escuro, tipo sombra da varanda
    frame[200:320, 260:380] = (255, 255, 255)  # "pássaro" claro no meio
    return frame


def main() -> None:
    shutil.rmtree(TMP_DIR, ignore_errors=True)
    TMP_DIR.mkdir(parents=True)

    identifier = build_identifier("mock")
    notifier = build_notifier("console")
    store = SightingsStore(TMP_DIR / "sightings.db")

    triggered = 0
    for i in range(2):
        # simula duas "visitas" (como se o AnimalDetector tivesse achado um
        # bicho nesses dois momentos)
        frame = make_frame()
        triggered += 1
        result = identifier.identify(frame)
        assert result is not None

        image_path = TMP_DIR / f"frame_{i}.jpg"
        cv2.imwrite(str(image_path), frame)

        sighting_count = store.count_sightings_of(result.species_common_name) + 1
        row_id = store.add(result, image_path)
        assert row_id is not None
        notifier.notify(result, image_path, sighting_count)

    assert store.all_species_count() >= 1
    recent = store.recent()
    assert len(recent) == triggered

    store.close()
    print(f"\nOK: {triggered} visita(s) processada(s) de ponta a ponta.")


if __name__ == "__main__":
    main()
