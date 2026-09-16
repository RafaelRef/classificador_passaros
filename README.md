# Pássaros App — protótipo (Fase 1 e 2)

Protótipo do sistema de identificação automática de pássaros pra varanda da casa
de praia. Pensado pra você testar agora mesmo com a webcam do seu computador, sem
precisar esperar o hardware de campo (ESP32-CAM) chegar.

## Arquitetura

```
webcam/ESP32-CAM -> detecção de ser vivo (YOLO) -> identificação de espécie -> log (SQLite) -> notificação
   capture.py           detector.py                  identify.py            storage.py         notify.py
```

Cada peça é trocável independente das outras (por isso as classes abstratas em
cada módulo). Isso significa: você pode trocar a webcam pelo ESP32-CAM, ou o
Telegram pelo WhatsApp, depois, sem reescrever o resto.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate   # no Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Rodar em modo 100% de teste (sem nenhuma credencial, só a webcam):

```bash
python -m src.main
```

Isso já valida o pipeline inteiro: câmera liga, detecta quando algo se move na
frente dela, "identifica" (modo mock, sem IA de verdade ainda) e imprime no
terminal + salva no banco (`data/sightings.db`) e a foto (`data/captures/`).

## Ligando a identificação de verdade (iNaturalist)

1. Crie uma conta em [inaturalist.org](https://www.inaturalist.org) (gratuita).
2. Gere um token em https://www.inaturalist.org/users/api_token (válido por
   ~24h).
3. No `.env`: `IDENTIFY_BACKEND=inaturalist` e `INATURALIST_TOKEN=<seu token>`.

O token expira a cada ~24h. Criar uma OAuth Application no iNaturalist pra
automatizar a renovação exige ser "App Owner" aprovado, o que só é liberado
depois de ~2 meses de conta — então em vez disso, a renovação é manual via
Telegram: quando o token expira, o app manda um aviso no grupo, você gera um
token novo (mesmo link do passo 2) e responde no grupo com
`/token SEU_TOKEN_AQUI`. O app salva esse valor em
`data/inaturalist_token.txt` e volta a identificar — sem precisar editar o
`.env` nem reiniciar nada. Precisa do Telegram configurado (próxima seção).

## Ligando a notificação de verdade (Telegram)

1. No Telegram, fale com **@BotFather**, mande `/newbot` e siga o passo a passo.
   Guarde o token que ele te der.
2. Adicione o bot a um grupo com a família (ou converse direto com ele).
3. Mande qualquer mensagem no grupo/chat, depois acesse
   `https://api.telegram.org/bot<SEU_TOKEN>/getUpdates` no navegador pra achar o
   `chat_id`.
4. No `.env`: `NOTIFY_BACKEND=telegram`, `TELEGRAM_BOT_TOKEN=...`,
   `TELEGRAM_CHAT_ID=...`.

## Sobre WhatsApp

Dá pra integrar, mas com um trade-off que vale considerar (ver comentário no
topo de `src/notify.py` para o detalhe completo): a via oficial (Meta Cloud API)
exige verificação de conta Business e aprovação de templates; a via não-oficial
(ex: whatsapp-web.js, em Node.js) é mais rápida de configurar mas roda por fora
do suporte oficial do WhatsApp. Recomendo validar tudo com Telegram primeiro, e
se depois de usar vocês preferirem mesmo WhatsApp, a gente troca só essa peça.

## Quando o hardware de campo chegar (ESP32-CAM)

- `capture.py` já tem um esqueleto pronto (`ESP32CamSource`) pra puxar imagens de
  um ESP32-CAM na rede local via HTTP — só apontar pra URL de captura do
  firmware da câmera.
- Como não tem tomada perto da estante (só Wi-Fi), o ESP32-CAM é a escolha mais
  sensata: consome muito menos energia que um Raspberry Pi, então uma bateria +
  painel solar pequeno dá conta. E como você já mexe com Arduino IDE, a curva de
  aprendizado é baixa (o ESP32-CAM é programado pela mesma IDE).
- O Arduino que você já tem pode ficar responsável só por um sensor de
  movimento físico (PIR) se quiser complementar a detecção por software — mas
  não é obrigatório, `detector.py` já resolve isso detectando o bicho direto
  na imagem (via YOLO).

## Próximos passos (Fase 3 em diante)

- Dashboard web lendo direto de `data/sightings.db`: linha do tempo de
  avistamentos, contagem de espécies diferentes, filtros por data.
- Deploy do pipeline rodando continuamente perto da câmera (ou num servidor,
  puxando imagens do ESP32-CAM pela rede).
