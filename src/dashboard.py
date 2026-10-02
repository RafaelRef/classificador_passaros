"""
Dashboard web somente-leitura, lendo direto de data/sightings.db.

Rodar (na raiz do projeto, com o venv ativado):

    python -m src.dashboard

Depois abrir http://localhost:5000 no navegador. Não precisa do pipeline
(src/main.py) rodando ao mesmo tempo — lê o banco direto, então funciona mesmo
com o app principal parado (só não vai ter avistamento novo aparecendo).
"""

from __future__ import annotations

import pathlib
from datetime import date, datetime, timedelta

from flask import Flask, abort, render_template, request, send_file

from .storage import SightingsStore

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "sightings.db"
CAPTURES_DIR = PROJECT_ROOT / "data" / "captures"

app = Flask(__name__, template_folder=str(PROJECT_ROOT / "src" / "templates"))

# Intervalo padrão quando a pessoa ainda não escolheu um filtro de data.
DEFAULT_RANGE_DAYS = 30


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


# Acima desses limites, agrupa por semana e depois por mês em vez de dia —
# senão o gráfico vira centenas/milhares de barras zeradas, fica ilegível e
# estica o layout da página (cada barra tem uma largura mínima em CSS).
MAX_DAILY_BARS = 62
MAX_WEEKLY_BARS = 60  # ~14 meses


def _build_day_bars(by_day: list[tuple[str, int]], start: date, end: date) -> list[dict]:
    counts = {d: n for d, n in by_day}
    total_days = (end - start).days + 1

    if total_days <= MAX_DAILY_BARS:
        bars = []
        for i in range(total_days):
            d = start + timedelta(days=i)
            bars.append({"label": d.strftime("%d/%m"), "n": counts.get(d.isoformat(), 0)})
    elif total_days <= MAX_WEEKLY_BARS * 7:
        weekly: dict[date, int] = {}
        d = start
        while d <= end:
            week_start = d - timedelta(days=d.weekday())
            weekly[week_start] = weekly.get(week_start, 0) + counts.get(d.isoformat(), 0)
            d += timedelta(days=1)
        bars = [
            {"label": week_start.strftime("%d/%m"), "n": n}
            for week_start, n in sorted(weekly.items())
        ]
    else:
        monthly: dict[str, int] = {}
        d = start
        while d <= end:
            month_key = d.strftime("%Y-%m")
            monthly[month_key] = monthly.get(month_key, 0) + counts.get(d.isoformat(), 0)
            d += timedelta(days=1)
        bars = [
            {"label": date.fromisoformat(f"{month_key}-01").strftime("%m/%Y"), "n": n}
            for month_key, n in sorted(monthly.items())
        ]

    max_n = max((b["n"] for b in bars), default=0)
    for b in bars:
        b["pct"] = round(b["n"] / max_n * 100) if max_n else 0
    return bars


@app.route("/")
def index():
    today = date.today()
    start = _parse_date(request.args.get("start")) or (today - timedelta(days=DEFAULT_RANGE_DAYS - 1))
    end = _parse_date(request.args.get("end")) or today
    if start > end:
        start, end = end, start

    store = SightingsStore(DB_PATH)
    sightings = store.sightings_in_range(start.isoformat(), end.isoformat())
    by_day = store.counts_by_day(start.isoformat(), end.isoformat())
    by_species = store.counts_by_species(start.isoformat(), end.isoformat())
    earliest = store.earliest_sighting_date() or today.isoformat()
    store.close()

    for sighting in sightings:
        sighting["image_filename"] = pathlib.Path(sighting["image_path"]).name

    day_bars = _build_day_bars(by_day, start, end)

    max_species_count = max((n for _, n in by_species), default=0)
    species_bars = [
        {
            "name": name,
            "n": n,
            "pct": round(n / max_species_count * 100) if max_species_count else 0,
        }
        for name, n in by_species
    ]

    most_recent = None
    if sightings:
        most_recent = datetime.fromisoformat(sightings[0]["seen_at"]).strftime("%d/%m %H:%M")

    presets = [
        {"label": "Hoje", "start": today.isoformat(), "end": today.isoformat()},
        {"label": "7 dias", "start": (today - timedelta(days=6)).isoformat(), "end": today.isoformat()},
        {"label": "30 dias", "start": (today - timedelta(days=29)).isoformat(), "end": today.isoformat()},
        {"label": "Tudo", "start": earliest, "end": today.isoformat()},
    ]

    return render_template(
        "dashboard.html",
        sightings=sightings,
        day_bars=day_bars,
        species_bars=species_bars,
        total=len(sightings),
        species_count=len(by_species),
        most_recent=most_recent,
        start=start.isoformat(),
        end=end.isoformat(),
        today=today.isoformat(),
        presets=presets,
    )


@app.route("/image/<path:filename>")
def image(filename: str):
    # Usa só o nome do arquivo (ignora qualquer diretório embutido no valor)
    # pra nunca servir um caminho fora de data/captures/.
    safe_name = pathlib.Path(filename).name
    path = CAPTURES_DIR / safe_name
    if not path.is_file():
        abort(404)
    return send_file(path)


def main() -> None:
    app.run(host="0.0.0.0", port=5050, debug=True)


if __name__ == "__main__":
    main()
