"""
Dashboard web somente-leitura, lendo direto de data/sightings.db.

Rodar (na raiz do projeto, com o venv ativado):

    python -m src.dashboard

Depois abrir http://localhost:5050 — ou, do celular, o IP da casa na porta 5050.
Não precisa do pipeline (src/main.py) rodando ao mesmo tempo: lê o banco direto,
então a página abre igual com a câmera desligada (só não entra avistamento novo)
e abre igual com o banco inexistente ou vazio (mostra um aviso no lugar).
"""

from __future__ import annotations

import functools
import io
import os
import pathlib
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode

from dotenv import load_dotenv
from flask import Flask, abort, jsonify, render_template, request, send_file

from .esp32cam import ESP32Cam
from .storage import SightingsStore

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "sightings.db"
# resolve() aqui pra poder comparar com o caminho resolvido do arquivo pedido na
# rota de imagem e provar que ele está dentro dessa pasta.
CAPTURES_DIR = (PROJECT_ROOT / "data" / "captures").resolve()

app = Flask(__name__, template_folder=str(PROJECT_ROOT / "src" / "templates"))

# A câmera é opcional: sem ESP32_CAM_URL no .env (quem roda só com a webcam, ou
# num clone novo pra mexer no dashboard) o cartão da câmera simplesmente não
# aparece, em vez de ficar piscando "sem resposta" pra sempre.
load_dotenv(PROJECT_ROOT / ".env")
camera = ESP32Cam(os.getenv("ESP32_CAM_URL"))

# Intervalo padrão quando a pessoa ainda não escolheu um filtro de data.
DEFAULT_RANGE_DAYS = 30

# Quantas fotos entram de uma vez no feed, e o teto por carregamento de página.
# A tabela antiga não tinha limite nenhum: "Tudo" montava uma <img> por linha do
# banco. Com um ano de uso isso é um megabyte atrás do outro no 4G da vó.
PAGINA = 60
LIMITE_MAXIMO = 360

# Lado maior da miniatura servida no feed. 480px cobre o card (~160 CSS px) até
# em tela de 3x, e corta o JPEG original de ~150 KB pra ~25 KB.
LADO_MINIATURA = 480

_DIAS_SEMANA = (
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
)
_MESES = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")

_FONTES = {
    "inaturalist": "iNaturalist",
    "mock": "identificação de teste",
    "fake-seed": "dado de exemplo",
}


# ---------------------------------------------------------------- utilidades


def _to_local(seen_at: str | None) -> datetime | None:
    """
    Converte o "seen_at" guardado no banco (UTC, veja SightingsStore.add) pro
    fuso da máquina que está servindo o dashboard — senão a família vê um
    pássaro das 7h da manhã como "10:00". Linhas antigas sem offset no texto são
    tratadas como UTC, que é o que elas sempre foram.

    Devolve None em vez de explodir se o texto estiver corrompido: uma linha
    estragada no banco pode custar um horário, não a página inteira.
    """
    try:
        dt = datetime.fromisoformat(seen_at)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone()


def _parse_date(value: str | None) -> date | None:
    """Data da query string, ou None se vier vazia, torta ou inexistente."""
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _parse_int(value: str | None, padrao: int, minimo: int, maximo: int) -> int:
    """Inteiro da query string, sempre dentro da faixa — "?limite=banana" vira o padrão."""
    try:
        n = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return padrao
    return max(minimo, min(maximo, n))


def _nome_arquivo(caminho: str) -> str:
    """
    Só o nome do arquivo, de um caminho que pode ter sido gravado em qualquer
    sistema operacional.

    pathlib.Path(...).name não serve aqui: o image_path foi escrito pela máquina
    que capturou, e um Path POSIX lendo "C:\\captures\\x.jpg" devolve a string
    inteira como se fosse o nome. Cortar nos dois separadores funciona nos dois
    lados, porque nem "/" nem "\\" podem aparecer dentro de um nome de arquivo.
    """
    return (caminho or "").replace("\\", "/").rsplit("/", 1)[-1]


def _url(**params) -> str:
    """URL do próprio dashboard, carregando só os filtros que estão ativos."""
    limpos = {k: v for k, v in params.items() if v}
    return ("/?" + urlencode(limpos)) if limpos else "/"


def _rotulo_dia(dia: date, hoje: date) -> str:
    """Cabeçalho de cada bloco do feed, do jeito que a família fala."""
    if dia == hoje:
        return "Hoje"
    if dia == hoje - timedelta(days=1):
        return "Ontem"
    return f"{_DIAS_SEMANA[dia.weekday()].capitalize()}, {dia.strftime('%d/%m')}"


def _tempo_relativo(quando: datetime, agora: datetime) -> str:
    """
    Tempo em linguagem de gente: "há 2 horas" responde "a câmera ainda está
    funcionando?" muito mais rápido que "28/09/2026 07:12".
    """
    minutos = (agora - quando).total_seconds() / 60
    if minutos < 2:
        return "agora mesmo"
    if minutos < 60:
        return f"há {int(minutos)} minutos"
    horas = minutos / 60
    if horas < 2:
        return "há 1 hora"
    if horas < 24:
        return f"há {int(horas)} horas"
    dias = int(horas // 24)
    if dias == 1:
        return "ontem"
    if dias < 30:
        return f"há {dias} dias"
    return "em " + quando.strftime("%d/%m/%Y")


def _certeza(valor: float) -> tuple[int, str, str]:
    """
    Confiança do modelo em palavra, não só em porcentagem.

    "47%" não diz nada pra quem não é técnico — e, pior, parece erro do sistema.
    A porcentagem continua existindo (aparece ao abrir a foto), mas o que fica
    embaixo do pássaro é o quanto dá pra acreditar naquele nome.
    """
    try:
        pct = max(0, min(100, round(float(valor) * 100)))
    except (TypeError, ValueError):
        pct = 0
    if pct >= 85:
        return pct, "quase certeza", "alta"
    if pct >= 60:
        return pct, "provável", "media"
    return pct, "palpite", "baixa"


# -------------------------------------------------------------- os gráficos


def _barras_periodo(por_dia, inicio: date, fim: date):
    """
    Barras de movimento no período, com no máximo ~63 delas.

    A granularidade acompanha o tamanho do intervalo (dia, semana, mês, ano)
    porque um ano em barras diárias vira 365 tracinhos ilegíveis. A soma é feita
    percorrendo os dias que o banco devolveu, nunca o calendário inteiro: assim
    um "?start=0001-01-01" na query string não põe o servidor pra iterar milhões
    de datas.
    """
    contagem = {}
    for dia_iso, n in por_dia:
        if dia_iso:
            contagem[dia_iso] = n

    total_dias = (fim - inicio).days + 1

    if total_dias <= 62:
        baldes = [inicio + timedelta(days=i) for i in range(total_dias)]
        chave = lambda d: d  # noqa: E731
        rotulo = lambda d: d.strftime("%d/%m")  # noqa: E731
    elif total_dias <= 434:  # até ~14 meses: por semana
        baldes = []
        cursor = inicio - timedelta(days=inicio.weekday())
        while cursor <= fim:
            baldes.append(cursor)
            cursor += timedelta(days=7)
        chave = lambda d: d - timedelta(days=d.weekday())  # noqa: E731
        rotulo = lambda d: d.strftime("%d/%m")  # noqa: E731
    elif total_dias <= 1900:  # até ~5 anos: por mês
        baldes = []
        ano, mes = inicio.year, inicio.month
        while (ano, mes) <= (fim.year, fim.month):
            baldes.append(date(ano, mes, 1))
            mes += 1
            if mes == 13:
                ano, mes = ano + 1, 1
        chave = lambda d: date(d.year, d.month, 1)  # noqa: E731
        rotulo = lambda d: f"{_MESES[d.month - 1]}/{d.strftime('%y')}"  # noqa: E731
    else:
        baldes = [date(a, 1, 1) for a in range(inicio.year, fim.year + 1)]
        chave = lambda d: date(d.year, 1, 1)  # noqa: E731
        rotulo = lambda d: str(d.year)  # noqa: E731

    soma = {b: 0 for b in baldes}
    for dia_iso, n in contagem.items():
        dia = _parse_date(dia_iso)
        if dia is None:
            continue
        alvo = chave(dia)
        if alvo in soma:
            soma[alvo] += n

    maior = max(soma.values(), default=0)
    barras = [
        {
            "rotulo": rotulo(b),
            "n": soma[b],
            "pct": round(soma[b] / maior * 100) if maior else 0,
        }
        for b in baldes
    ]

    total = sum(soma.values())
    if not total:
        return barras, ""
    pico = max(baldes, key=lambda b: soma[b])
    media = f"{total / total_dias:.1f}".replace(".", ",")
    return barras, f"Dia mais cheio: {soma[pico]} em {rotulo(pico)}. Média de {media} por dia."


def _barras_hora(por_hora):
    """As 24 horas do dia, sempre todas, pra dar pra ver o formato do dia inteiro."""
    contagem = {}
    for hora, n in por_hora:
        try:
            contagem[int(hora)] = n
        except (TypeError, ValueError):
            continue

    maior = max(contagem.values(), default=0)
    barras = [
        {
            "hora": h,
            "rotulo": f"{h:02d}h",
            "n": contagem.get(h, 0),
            "pct": round(contagem.get(h, 0) / maior * 100) if maior else 0,
        }
        for h in range(24)
    ]
    if not maior:
        return barras, ""
    pico = max(range(24), key=lambda h: contagem.get(h, 0))
    return barras, f"Aparecem mais por volta das {pico:02d}h."


# ------------------------------------------------------------------- rotas


@app.route("/")
def index():
    hoje = date.today()
    agora = datetime.now().astimezone()

    store = SightingsStore(DB_PATH, readonly=True)
    try:
        sem_banco = store.sem_banco
        mais_antigo = _parse_date(store.earliest_sighting_date())

        # --- período -------------------------------------------------------
        # Data inválida ("?start=31/02/2026") vira None e cai no padrão; faltar
        # uma das duas também. Nenhum caminho aqui levanta exceção.
        inicio_req = _parse_date(request.args.get("start"))
        fim_req = _parse_date(request.args.get("end"))
        inicio = inicio_req or (fim_req or hoje) - timedelta(days=DEFAULT_RANGE_DAYS - 1)
        fim = fim_req or hoje

        # Intervalo invertido: em vez de devolver vazio (que parece defeito),
        # troca as pontas e avisa, que é o que a pessoa quis dizer.
        datas_invertidas = bool(inicio_req and fim_req and inicio_req > fim_req)
        if inicio > fim:
            inicio, fim = fim, inicio

        if fim > hoje:
            fim = hoje
        # Teto de sanidade: sem isso "?start=0001-01-01" faria o gráfico montar
        # milhares de baldes e a página levar segundos pra abrir. 25 anos é bem
        # mais do que qualquer histórico que esse projeto possa ter.
        piso = fim - timedelta(days=366 * 25)
        if inicio < piso:
            inicio = piso
        if inicio > fim:
            inicio = fim

        # --- filtro por espécie --------------------------------------------
        # Um nome que não existe no banco não é erro: simplesmente não casa com
        # nada, a página mostra o estado vazio e o link de limpar o filtro.
        especie = (request.args.get("especie") or "").strip()
        limite = _parse_int(request.args.get("limite"), PAGINA, PAGINA, LIMITE_MAXIMO)

        inicio_iso, fim_iso = inicio.isoformat(), fim.isoformat()
        filtro = especie or None

        total = store.count_in_range(inicio_iso, fim_iso, filtro)
        linhas = store.sightings_in_range(inicio_iso, fim_iso, filtro, limit=limite)
        por_dia = store.counts_by_day(inicio_iso, fim_iso, filtro)
        por_hora = store.counts_by_hour(inicio_iso, fim_iso, filtro)
        por_especie = store.counts_by_species(inicio_iso, fim_iso)
        primeiras = store.first_sighting_per_species()
        ultimo = store.latest_sighting()
    finally:
        store.close()

    banco_vazio = mais_antigo is None and not sem_banco

    # --- "1ª vez que vemos essa espécie" ------------------------------------
    ids_primeira = {p["id"] for p in primeiras}
    novas_no_periodo = set()
    for p in primeiras:
        quando = _to_local(p["seen_at"])
        if quando and inicio <= quando.date() <= fim:
            novas_no_periodo.add(p["species_common_name"])

    # --- feed agrupado por dia ----------------------------------------------
    grupos: list[dict] = []
    for linha in linhas:
        quando = _to_local(linha["seen_at"])
        if quando is None:
            # A consulta já descarta linha com data ilegível (date() devolve
            # NULL e a comparação não casa); isto é só o cinto de segurança.
            continue
        pct, rotulo_certeza, classe_certeza = _certeza(linha["confidence"])
        fonte = _FONTES.get(linha["source"], linha["source"])
        item = {
            "arquivo": _nome_arquivo(linha["image_path"]),
            "nome": linha["species_common_name"],
            "cientifico": linha["species_scientific_name"] or "",
            "hora": quando.strftime("%H:%M"),
            "quando": quando.strftime("%d/%m/%Y às %H:%M"),
            "detalhe": f"{quando.strftime('%d/%m/%Y às %H:%M')} · {pct}% de certeza · {fonte}",
            "certeza_pct": pct,
            "certeza_rotulo": rotulo_certeza,
            "certeza_classe": classe_certeza,
            "primeira_vez": linha["id"] in ids_primeira,
        }
        dia = quando.date()
        # As linhas já vêm ordenadas por data decrescente, então basta comparar
        # com o último grupo aberto pra montar os blocos numa passada só.
        if not grupos or grupos[-1]["data"] != dia:
            grupos.append({"data": dia, "rotulo": _rotulo_dia(dia, hoje), "itens": []})
        grupos[-1]["itens"].append(item)

    mostrando = sum(len(g["itens"]) for g in grupos)

    # --- navegação -----------------------------------------------------------
    url_tudo = _url(start=(mais_antigo or hoje).isoformat(), end=hoje.isoformat())
    atalhos_base = [
        ("Hoje", hoje, hoje),
        ("7 dias", hoje - timedelta(days=6), hoje),
        ("30 dias", hoje - timedelta(days=29), hoje),
        ("Tudo", mais_antigo or hoje, hoje),
    ]
    atalhos = [
        {
            "label": label,
            "url": _url(start=a.isoformat(), end=b.isoformat(), especie=especie),
            "ativo": a == inicio and b == fim,
        }
        for label, a, b in atalhos_base
    ]

    maior_especie = max((n for _, n in por_especie), default=0)
    ranking = [
        {
            "nome": nome,
            "n": n,
            "pct": round(n / maior_especie * 100) if maior_especie else 0,
            "nova": nome in novas_no_periodo,
            "ativa": nome == especie,
            # Tocar na espécie que já está filtrando desliga o filtro: é o
            # mesmo botão pra ligar e desligar, que é como as pessoas esperam.
            "url": _url(
                start=inicio_iso,
                end=fim_iso,
                especie=None if nome == especie else nome,
            ),
        }
        for nome, n in por_especie
    ]

    # Mais fotos sem sair da página; e, quando o teto por carregamento é
    # atingido, um salto pra trás no tempo em vez de um beco sem saída.
    url_mais = None
    url_anteriores = None
    anteriores_desde = None
    if mostrando < total:
        if limite < LIMITE_MAXIMO:
            url_mais = _url(
                start=inicio_iso, end=fim_iso, especie=especie, limite=limite + PAGINA
            )
        elif grupos:
            borda = grupos[-1]["data"]
            url_anteriores = _url(
                start=inicio_iso, end=borda.isoformat(), especie=especie
            )
            anteriores_desde = borda.strftime("%d/%m/%Y")

    ultimo_quando = _to_local(ultimo["seen_at"]) if ultimo else None
    if ultimo and ultimo_quando:
        status = (
            f"Último pássaro: {ultimo['species_common_name']}, "
            f"{_tempo_relativo(ultimo_quando, agora)}."
        )
    else:
        status = "Nenhum pássaro registrado ainda."

    barras_dia, legenda_dia = _barras_periodo(por_dia, inicio, fim)
    barras_hora, legenda_hora = _barras_hora(por_hora)

    return render_template(
        "dashboard.html",
        inicio=inicio_iso,
        fim=fim_iso,
        # Só se a câmera está configurada. O estado dela vem depois, por JS: ler
        # o ESP aqui somaria o timeout (2s, quando ele está dormindo) ao
        # carregamento de toda página.
        tem_camera=camera.configurado,
        hoje=hoje.isoformat(),
        especie=especie,
        atalhos=atalhos,
        # Abre o seletor de datas sozinho quando o período não é nenhum dos
        # atalhos — senão a pessoa não vê qual intervalo está valendo.
        intervalo_aberto=not any(a["ativo"] for a in atalhos),
        datas_invertidas=datas_invertidas,
        sem_banco=sem_banco,
        banco_vazio=banco_vazio,
        status=status,
        resumo={
            "total": total,
            "rotulo_total": f"Fotos de {especie}" if especie else "Avistamentos",
            "especies": len(por_especie),
            "novas": len(novas_no_periodo),
        },
        ranking_topo=ranking[:6],
        ranking_resto=ranking[6:],
        grupos=grupos,
        mostrando=mostrando,
        total=total,
        url_mais=url_mais,
        url_anteriores=url_anteriores,
        anteriores_desde=anteriores_desde,
        url_limpar=_url(start=inicio_iso, end=fim_iso),
        url_tudo=url_tudo,
        barras_dia=barras_dia,
        legenda_dia=legenda_dia,
        barras_hora=barras_hora,
        legenda_hora=legenda_hora,
    )


# --------------------------------------------------------------- as imagens

_EXTENSOES = {".jpg", ".jpeg", ".png", ".webp"}


def _captura(filename: str) -> pathlib.Path:
    """
    Resolve o nome pedido dentro de data/captures/, ou 404.

    Três travas em cima da mesma coisa, porque é a única rota que toca o disco:
    (1) joga fora qualquer diretório embutido no valor, nos dois separadores;
    (2) exige extensão de imagem; (3) resolve o caminho final e confere que o
    pai é exatamente data/captures/ — o que também derruba link simbólico
    apontando pra fora. A rota usa <filename> (e não <path:filename>), então
    barra nem chega aqui.
    """
    nome = _nome_arquivo(filename)
    if not nome or nome in {".", ".."}:
        abort(404)
    if pathlib.Path(nome).suffix.lower() not in _EXTENSOES:
        abort(404)
    caminho = (CAPTURES_DIR / nome).resolve()
    if caminho.parent != CAPTURES_DIR or not caminho.is_file():
        abort(404)
    return caminho


# O nome do arquivo carrega o timestamp da captura, então o conteúdo nunca muda:
# deixar o celular guardar em cache poupa o download inteiro a cada recarga.
CACHE_FOTOS = 60 * 60 * 24 * 30


@app.route("/image/<filename>")
def image(filename: str):
    """A foto original, em tamanho cheio — é a que abre ao tocar na miniatura."""
    return send_file(_captura(filename), max_age=CACHE_FOTOS)


def _opencv():
    """
    Importa o OpenCV só na primeira miniatura pedida.

    Importar cv2 custa quase um segundo e o dashboard não precisa dele pra
    subir. Se a máquina que serve a página nem tiver o OpenCV, a rota de
    miniatura cai pro arquivo original em vez de a página quebrar.
    """
    import cv2
    import numpy

    return cv2, numpy


@functools.lru_cache(maxsize=256)
def _miniatura(nome: str, assinatura: tuple) -> bytes:
    """
    Miniatura JPEG de uma captura, guardada em memória.

    Existe porque a página servia o JPEG inteiro e mandava o navegador encolher
    pra 48px: no celular isso é a página inteira de fotos em tamanho real
    atravessando o wi-fi pra virar selo de 48 pixels.

    'assinatura' (mtime + tamanho) entra na chave só pra o cache se invalidar
    sozinho se o arquivo for trocado por outro com o mesmo nome. Cache em
    memória, e não em disco, porque o dashboard não escreve nada em lugar
    nenhum — e 256 miniaturas de ~25 KB cabem folgado na RAM.

    Lê os bytes e usa imdecode em vez de cv2.imread pelo mesmo motivo que
    src/imageio.py evita cv2.imwrite: o OpenCV não lida com acento no caminho
    (e esse projeto mora numa pasta "Área de Trabalho"), e falha calado.
    """
    cv2, numpy = _opencv()
    dados = (CAPTURES_DIR / nome).read_bytes()
    imagem = cv2.imdecode(numpy.frombuffer(dados, numpy.uint8), cv2.IMREAD_COLOR)
    if imagem is None:
        raise ValueError(f"não consegui decodificar a captura: {nome}")
    altura, largura = imagem.shape[:2]
    maior = max(altura, largura)
    if maior > LADO_MINIATURA:
        escala = LADO_MINIATURA / maior
        imagem = cv2.resize(
            imagem,
            (max(1, round(largura * escala)), max(1, round(altura * escala))),
            interpolation=cv2.INTER_AREA,
        )
    ok, buffer = cv2.imencode(".jpg", imagem, [int(cv2.IMWRITE_JPEG_QUALITY), 72])
    if not ok:
        raise ValueError(f"não consegui recodificar a miniatura: {nome}")
    return buffer.tobytes()


@app.route("/thumb/<filename>")
def thumb(filename: str):
    """Versão leve da foto, usada na grade."""
    caminho = _captura(filename)
    try:
        info = caminho.stat()
        dados = _miniatura(caminho.name, (info.st_mtime_ns, info.st_size))
    except Exception:
        # OpenCV ausente, JPEG corrompido, o que for: serve o original. A
        # família continua vendo o pássaro, só gasta mais banda.
        return send_file(caminho, max_age=CACHE_FOTOS)
    return send_file(
        io.BytesIO(dados),
        mimetype="image/jpeg",
        max_age=CACHE_FOTOS,
        conditional=False,
    )


# ---------------------------------------------------------------- a câmera
# Três rotas finas em cima do ESP32Cam. Elas existem pro navegador nunca falar
# direto com o ESP: ele aguenta uma conexão por vez, e o celular de cada pessoa
# da casa com o dashboard aberto seria uma conexão a mais.


@app.route("/camera/estado")
def camera_estado():
    return jsonify(camera.estado())


@app.route("/camera/keepalive", methods=["POST"])
def camera_keepalive():
    """
    Enquanto a janela ao vivo estiver aberta, o navegador chama isso de tempos
    em tempos pra segurar o ESP acordado — sem isso ele dorme 10s depois do
    bicho sair do quadro, bem no meio de alguém assistindo.
    """
    return jsonify(camera.keepalive())


@app.route("/camera/frame")
def camera_frame():
    """
    Um JPEG da câmera agora. 503 quando não deu: é o sinal pro JS da janela ao
    vivo parar de insistir e mostrar que a câmera saiu do ar.
    """
    imagem = camera.frame()
    if imagem is None:
        abort(503)
    resposta = app.response_class(imagem, mimetype="image/jpeg")
    # Cada chamada é um frame novo de uma câmera ao vivo: cache aqui serviria
    # só pra mostrar foto velha.
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


def main() -> None:
    # host="0.0.0.0" pra dar pra abrir do celular na mesma rede de casa. Por isso
    # mesmo, use_debugger fica desligado: o console interativo do Werkzeug que
    # vem junto com debug=True executa Python arbitrário, e aqui ele estaria
    # exposto pra rede inteira. O reloader (prático pra mexer no dashboard) não
    # tem esse problema e continua ligado.
    app.run(host="0.0.0.0", port=5050, use_reloader=True, use_debugger=False)


if __name__ == "__main__":
    main()
