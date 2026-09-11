"""
Loop principal: captura -> detecta movimento -> identifica -> loga -> notifica.

Rodar (na raiz do projeto, com o venv ativado e as dependências instaladas):

    python -m src.main

Por padrão roda 100% em modo "mock" (identificação falsa) + "console" (só imprime
no terminal), então dá pra testar sem configurar nada além de ter uma webcam.
Ajuste o .env quando quiser ligar a identificação e a notificação de verdade.
"""

from __future__ import annotations

import os
import pathlib
import queue
import threading
import time

import cv2
from dotenv import load_dotenv

from .capture import WebcamSource
from .identify import Identifier
from .identify import build_identifier
from .motion import MotionDetector
from .notify import Notifier
from .notify import build_notifier
from .storage import SightingsStore

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
CAPTURES_DIR = PROJECT_ROOT / "data" / "captures"
DB_PATH = PROJECT_ROOT / "data" / "sightings.db"

# Intervalo mínimo entre identificações, pra não disparar a API várias vezes
# seguidas pro mesmo pássaro parado no prato.
COOLDOWN_SECONDS = 20


def _identify_worker(
    jobs: queue.Queue,
    identifier: Identifier,
    notifier: Notifier,
    store: SightingsStore,
) -> None:
    """
    Roda em thread separada: identificar espécie + salvar + notificar envolve
    chamadas de rede (~poucos segundos). Isso fica isolado aqui pra não travar
    o loop principal, que precisa continuar lendo a câmera e detectando
    movimento o tempo todo, mesmo enquanto o avistamento anterior ainda está
    sendo processado.
    """
    while True:
        frame, image_path = jobs.get()
        if frame is None:
            return

        result = identifier.identify(frame)
        if result is None:
            print("[info] movimento detectado, mas não consegui identificar a espécie")
            continue

        cv2.imwrite(str(image_path), frame)

        sighting_count = store.count_sightings_of(result.species_common_name) + 1
        store.add(result, image_path)

        conf_pct = round(result.confidence * 100)
        print(
            f"[avistamento] {result.species_common_name} "
            f"({result.species_scientific_name or 's/ nome científico'}) - confiança: {conf_pct}%"
        )

        notifier.notify(result, image_path, sighting_count)


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")

    camera_index = int(os.getenv("CAMERA_INDEX", "0"))
    identify_backend = os.getenv("IDENTIFY_BACKEND", "mock")
    notify_backend = os.getenv("NOTIFY_BACKEND", "console")

    source = WebcamSource(camera_index=camera_index)
    motion = MotionDetector()
    identifier = build_identifier(
        identify_backend, inaturalist_token=os.getenv("INATURALIST_TOKEN")
    )
    notifier = build_notifier(
        notify_backend,
        telegram_token=os.getenv("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID"),
    )
    store = SightingsStore(DB_PATH)
    CAPTURES_DIR.mkdir(parents=True, exist_ok=True)

    jobs: queue.Queue = queue.Queue()
    worker = threading.Thread(
        target=_identify_worker, args=(jobs, identifier, notifier, store), daemon=True
    )
    worker.start()

    print(
        f"[passaros-app] iniciado. identify={identify_backend} notify={notify_backend}. "
        "Ctrl+C para parar."
    )

    last_trigger = 0.0
    try:
        while True:
            frame = source.read()
            if frame is None:
                print("[aviso] não consegui ler frame da câmera, tentando de novo...")
                time.sleep(1)
                continue

            moved = motion.detected(frame)

            if moved and (time.time() - last_trigger) > COOLDOWN_SECONDS:
                last_trigger = time.time()
                image_path = CAPTURES_DIR / f"{int(time.time())}.jpg"
                jobs.put((frame.copy(), image_path))

            preview = frame.copy()
            if moved:
                cv2.putText(
                    preview, "MOVIMENTO DETECTADO", (20, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 2,
                )
            cv2.imshow("passaros-app - camera (pressione 'q' para sair)", preview)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                print("\n[passaros-app] encerrando...")
                break
    except KeyboardInterrupt:
        print("\n[passaros-app] encerrando...")
    finally:
        source.release()
        cv2.destroyAllWindows()
        store.close()


if __name__ == "__main__":
    main()
