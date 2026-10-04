"""
Loop principal: captura -> detecta movimento -> identifica -> loga -> notifica.

Rodar (na raiz do projeto, com o venv ativado e as dependências instaladas):

    python -m src.main

Por padrão roda 100% em modo "mock" (identificação falsa) + "console" (só imprime
no terminal), então dá pra testar sem configurar nada além de ter uma webcam.
Ajuste o .env quando quiser ligar a identificação e a notificação de verdade.
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
import queue
import threading
import time

import cv2
from dotenv import load_dotenv

from .capture import build_source
from .detector import AnimalDetector
from .identify import Identifier
from .identify import InaturalistTokenExpired
from .identify import build_identifier
from .imageio import write_image
from .notify import Notifier
from .notify import build_notifier
from .storage import SightingsStore
from .telegram_token_listener import TelegramTokenListener
from .telegram_token_listener import send_telegram_message
from .token_store import TokenStore

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
CAPTURES_DIR = PROJECT_ROOT / "data" / "captures"
DB_PATH = PROJECT_ROOT / "data" / "sightings.db"
INATURALIST_TOKEN_PATH = PROJECT_ROOT / "data" / "inaturalist_token.txt"

# Quanto tempo sem detecção um objeto rastreado pode ficar antes de
# considerarmos que ele foi embora. Enquanto ele ficar ali (mesmo que por
# minutos comendo no prato, parado) não identifica de novo; só quando ele sai
# do quadro por mais desse tempo (ou some da detecção do YOLO) é que uma nova
# aparição naquele lugar dispara uma nova identificação. Também cobre frames
# raros em que a detecção falhe por um instante (flicker).
ABSENCE_TO_RESET_SECONDS = 3

# Distância (em pixels, entre os centros das caixas) até a qual uma detecção
# neste frame é considerada "o mesmo objeto" que já estava sendo rastreado no
# frame anterior, em vez de um objeto novo. Sem isso, cada pássaro só seria
# identificado se aparecesse no exato frame em que o primeiro chegou — na
# prática eles quase nunca chegam no mesmo frame, então cada um precisa ser
# comparado com o que já é conhecido pra saber se é "novo" ou não.
MATCH_DISTANCE_PX = 200

# Margem (em pixels) adicionada ao redor de cada caixa detectada antes de
# recortar, pra não cortar pedaço do bicho quando a caixa do YOLO ficou justa.
CROP_PADDING = 30

# Cores (BGR) pras caixas de cada objeto detectado no preview — uma por objeto,
# ciclando se tiver mais objetos que cores.
BOX_COLORS = [
    (0, 0, 255),    # vermelho
    (0, 255, 0),    # verde
    (255, 0, 0),    # azul
    (0, 255, 255),  # amarelo
    (255, 0, 255),  # magenta
]

Box = tuple[int, int, int, int]


@dataclasses.dataclass
class _TrackedObject:
    box: Box
    last_seen: float


def _box_center(box: Box) -> tuple[float, float]:
    x, y, w, h = box
    return (x + w / 2, y + h / 2)


def _center_distance(a: Box, b: Box) -> float:
    (ax, ay), (bx, by) = _box_center(a), _box_center(b)
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def _find_closest(box: Box, candidates: list[_TrackedObject]) -> _TrackedObject | None:
    best, best_dist = None, MATCH_DISTANCE_PX
    for obj in candidates:
        dist = _center_distance(box, obj.box)
        if dist <= best_dist:
            best, best_dist = obj, dist
    return best


def _crop_with_padding(frame, box: Box, padding: int):
    x, y, w, h = box
    height, width = frame.shape[:2]
    x0, y0 = max(0, x - padding), max(0, y - padding)
    x1, y1 = min(width, x + w + padding), min(height, y + h + padding)
    return frame[y0:y1, x0:x1]

# Intervalo mínimo entre avisos de "token expirado" no Telegram, pra não
# spammar o grupo toda vez que um pássaro se mexe na frente da câmera.
TOKEN_WARNING_COOLDOWN_SECONDS = 600


def _identify_worker(
    jobs: queue.Queue,
    identifier: Identifier,
    notifier: Notifier,
    store: SightingsStore,
    telegram_token: str | None,
    telegram_chat_id: str | None,
) -> None:
    """
    Roda em thread separada: identificar espécie + salvar + notificar envolve
    chamadas de rede (~poucos segundos). Isso fica isolado aqui pra não travar
    o loop principal, que precisa continuar lendo a câmera e detectando
    movimento o tempo todo, mesmo enquanto o avistamento anterior ainda está
    sendo processado.
    """
    last_token_warning = 0.0

    while True:
        frame, image_path = jobs.get()
        if frame is None:
            return

        try:
            result = identifier.identify(frame)
        except InaturalistTokenExpired:
            print("[aviso] token do iNaturalist expirado ou ausente")
            if telegram_token and telegram_chat_id and (time.time() - last_token_warning) > TOKEN_WARNING_COOLDOWN_SECONDS:
                last_token_warning = time.time()
                send_telegram_message(
                    telegram_token,
                    telegram_chat_id,
                    "⚠️ Token do iNaturalist expirou. Gere um novo em "
                    "https://www.inaturalist.org/users/api_token e manda aqui "
                    "assim: /token SEU_TOKEN_AQUI",
                )
            continue

        if result is None:
            print("[info] movimento detectado, mas não consegui identificar a espécie")
            continue

        write_image(image_path, frame)

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

    camera_source = os.getenv("CAMERA_SOURCE", "webcam")
    camera_index = int(os.getenv("CAMERA_INDEX", "0"))
    esp32_cam_url = os.getenv("ESP32_CAM_URL")
    identify_backend = os.getenv("IDENTIFY_BACKEND", "mock")
    notify_backend = os.getenv("NOTIFY_BACKEND", "console")
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
    ignore_humans = os.getenv("IGNORE_HUMANS", "true").strip().lower() not in ("false", "0", "no")

    source = build_source(camera_source, camera_index=camera_index, esp32_cam_url=esp32_cam_url)
    detector = AnimalDetector(ignore_humans=ignore_humans)

    token_store = TokenStore(INATURALIST_TOKEN_PATH, initial_token=os.getenv("INATURALIST_TOKEN", ""))
    identifier = build_identifier(identify_backend, inaturalist_token_provider=token_store.get)
    notifier = build_notifier(
        notify_backend,
        telegram_token=telegram_token,
        telegram_chat_id=telegram_chat_id,
    )
    store = SightingsStore(DB_PATH)
    CAPTURES_DIR.mkdir(parents=True, exist_ok=True)

    if identify_backend == "inaturalist" and telegram_token and telegram_chat_id:
        TelegramTokenListener(telegram_token, telegram_chat_id, token_store).start()
        print("[passaros-app] renovação de token via Telegram ativa (manda /token SEU_TOKEN)")

    jobs: queue.Queue = queue.Queue()
    worker = threading.Thread(
        target=_identify_worker,
        args=(jobs, identifier, notifier, store, telegram_token, telegram_chat_id),
        daemon=True,
    )
    worker.start()

    print(
        f"[passaros-app] iniciado. identify={identify_backend} notify={notify_backend} "
        f"ignore_humans={ignore_humans}. Ctrl+C para parar."
    )

    tracked: list[_TrackedObject] = []
    next_object_id = 0
    try:
        while True:
            frame = source.read()
            if frame is None:
                print("[aviso] não consegui ler frame da câmera, tentando de novo...")
                time.sleep(1)
                continue

            detections = detector.detect(frame)
            now = time.time()

            # Casa cada detecção deste frame com um objeto já rastreado (pela
            # proximidade da caixa); o que sobrar não casado é objeto novo.
            unmatched_tracked = list(tracked)
            for det in detections:
                match = _find_closest(det.box, unmatched_tracked)
                if match is not None:
                    match.box = det.box
                    match.last_seen = now
                    unmatched_tracked.remove(match)
                else:
                    next_object_id += 1
                    tracked.append(_TrackedObject(box=det.box, last_seen=now))
                    crop = _crop_with_padding(frame, det.box, CROP_PADDING)
                    image_path = CAPTURES_DIR / f"{int(now)}_{next_object_id}.jpg"
                    jobs.put((crop.copy(), image_path))
                    print(f"[info] objeto novo detectado ({det.class_name}), identificando...")

            # Objetos rastreados que não bateram com nenhuma detecção deste
            # frame por tempo demais: consideramos que foram embora. Uma nova
            # aparição no mesmo lugar depois disso volta a contar como "novo".
            still_tracked = []
            for obj in tracked:
                if now - obj.last_seen > ABSENCE_TO_RESET_SECONDS:
                    print(
                        f"[info] objeto saiu do quadro (sem detecção por mais de "
                        f"{ABSENCE_TO_RESET_SECONDS}s) — próxima aparição ali conta como novo"
                    )
                else:
                    still_tracked.append(obj)
            tracked = still_tracked

            preview = frame.copy()
            for i, det in enumerate(detections):
                x, y, w, h = det.box
                color = BOX_COLORS[i % len(BOX_COLORS)]
                cv2.rectangle(preview, (x, y), (x + w, y + h), color, 2)
                label = f"{det.class_name} {round(det.confidence * 100)}%"
                cv2.putText(
                    preview, label, (x, max(20, y - 10)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2,
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
