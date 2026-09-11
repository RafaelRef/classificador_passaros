"""
Smoke test manual: não usa webcam de verdade (não tem uma disponível neste
ambiente), mas exercita motion.py, identify.py, storage.py e notify.py com
frames sintéticos, simulando um pipeline completo de ponta a ponta.

Rodar: python -m tests_manual.smoke_test
"""

import pathlib
import shutil
import sys

import cv2
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.identify import build_identifier
from src.motion import MotionDetector
from src.notify import build_notifier
from src.storage import SightingsStore

TMP_DIR = pathlib.Path(__file__).resolve().parent / "_tmp"


def make_frame(offset: int) -> np.ndarray:
    """Frame sintético em resolução realista (tipo webcam 480p) com um 'objeto' que se move."""
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    frame[:, :] = (20, 20, 20)  # fundo escuro, tipo sombra da varanda
    x = 100 + offset
    frame[200:320, x : x + 120] = (255, 255, 255)  # "pássaro" bem claro se movendo
    return frame


def main() -> None:
    shutil.rmtree(TMP_DIR, ignore_errors=True)
    TMP_DIR.mkdir(parents=True)

    motion = MotionDetector()  # parâmetros padrão, tamanho de frame realista agora
    identifier = build_identifier("mock")
    notifier = build_notifier("console")
    store = SightingsStore(TMP_DIR / "sightings.db")

    triggered = 0
    for i, frame in enumerate([make_frame(0), make_frame(0), make_frame(60), make_frame(60)]):
        moved = motion.detected(frame)
        print(f"frame {i}: movimento detectado = {moved}")
        if moved:
            triggered += 1
            result = identifier.identify(frame)
            assert result is not None

            image_path = TMP_DIR / f"frame_{i}.jpg"
            cv2.imwrite(str(image_path), frame)

            sighting_count = store.count_sightings_of(result.species_common_name) + 1
            row_id = store.add(result, image_path)
            assert row_id is not None
            notifier.notify(result, image_path, sighting_count)

    assert triggered >= 1, "esperava pelo menos 1 disparo de movimento"
    assert store.all_species_count() >= 1
    recent = store.recent()
    assert len(recent) == triggered

    store.close()
    print(f"\nOK: {triggered} disparo(s) de movimento processado(s) de ponta a ponta.")


if __name__ == "__main__":
    main()
