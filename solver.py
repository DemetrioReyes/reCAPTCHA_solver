"""
Solver PROPIO de reCAPTCHA v2 vía CDP (Chrome DevTools Protocol).
Chrome + WebSocket + requests. Sin Selenium.

Requisitos:
  pip install websockets requests SpeechRecognition imageio-ffmpeg
  Chrome instalado
"""

import os
import sys
import json
import shutil
import socket
import subprocess
import asyncio
import logging
import tempfile

import requests
import websockets
import speech_recognition as sr

logger = logging.getLogger("solver")

# Config
TIMEOUT = 20
MAX_INTENTOS = 5

# Cuántas resoluciones (cada una lanza su propio Chrome) pueden correr en paralelo.
MAX_CONCURRENT_SOLVES = int(os.environ.get("MAX_CONCURRENT_SOLVES", "3"))
_SOLVE_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_SOLVES)

# Reintentos (con Chrome nuevo) si una resolución completa falla o no logra token.
RECAPTCHA_RETRIES = int(os.environ.get("RECAPTCHA_RETRIES", "1"))

CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expanduser(r"~\AppData\Local\Google\Chrome\Application\chrome.exe"),
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]


def _find_chrome() -> str:
    for path in CHROME_PATHS:
        if os.path.exists(path):
            return path
    raise FileNotFoundError("Chrome no encontrado")


def _free_port() -> int:
    """Puerto TCP libre en localhost, para no chocar entre resoluciones concurrentes."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _launch_chrome(port: int) -> tuple[subprocess.Popen, str]:
    chrome = _find_chrome()
    user_data_dir = tempfile.mkdtemp(prefix="chrome_cdp_")
    cmd = [
        chrome,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run", "--no-default-browser-check",
        "--disable-extensions", "--disable-sync",
        "--window-size=1366,768", "about:blank",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc, user_data_dir


async def _get_ws_url(port: int) -> str:
    for _ in range(10):
        try:
            resp = await asyncio.to_thread(requests.get, f"http://localhost:{port}/json/version", timeout=2)
            return resp.json()["webSocketDebuggerUrl"]
        except Exception:
            await asyncio.sleep(1)
    raise Exception("No se pudo conectar a Chrome CDP")


class CDPClient:
    def __init__(self, ws_url: str):
        self.ws_url = ws_url
        self.ws = None
        self._id = 0

    async def connect(self):
        self.ws = await websockets.connect(self.ws_url, max_size=50 * 1024 * 1024)

    async def send(self, method: str, params: dict = None) -> dict:
        self._id += 1
        msg = {"id": self._id, "method": method}
        if params:
            msg["params"] = params
        await self.ws.send(json.dumps(msg))
        while True:
            resp = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=TIMEOUT))
            if resp.get("id") == self._id:
                if resp.get("error"):
                    raise Exception(f"CDP error: {resp['error']}")
                return resp.get("result", {})

    async def evaluate(self, expression: str):
        result = await self.send("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True,
        })
        if "exceptionDetails" in result:
            raise Exception(f"JS error: {result['exceptionDetails']}")
        return result.get("result", {}).get("value")

    async def close(self):
        if self.ws:
            await self.ws.close()


async def _click_checkbox(cdp: CDPClient):
    tree = await cdp.send("Page.getFrameTree")
    anchor = None

    def find(node):
        nonlocal anchor
        frame = node.get("frame", {})
        if "recaptcha/api2/anchor" in frame.get("url", ""):
            anchor = frame
        for child in node.get("childFrames", []):
            find(child)

    find(tree.get("frameTree", {}))
    if not anchor:
        raise Exception("No se encontró iframe anchor")

    ctx = await cdp.send("Page.createIsolatedWorld", {"frameId": anchor["id"], "grantUniveralAccess": True})
    await cdp.send("Runtime.evaluate", {
        "expression": "document.querySelector('.recaptcha-checkbox-border')?.click()",
        "contextId": ctx["executionContextId"], "returnByValue": True,
    })
    await asyncio.sleep(3)


async def _resolver_audio(cdp: CDPClient) -> str:
    for intento in range(MAX_INTENTOS):
        logger.info("Intento %d de audio", intento + 1)

        tree = await cdp.send("Page.getFrameTree")
        bframe = None

        def find_b(node):
            nonlocal bframe
            frame = node.get("frame", {})
            if "recaptcha/api2/bframe" in frame.get("url", ""):
                bframe = frame
            for child in node.get("childFrames", []):
                find_b(child)

        find_b(tree.get("frameTree", {}))
        if not bframe:
            return ""

        ctx = await cdp.send("Page.createIsolatedWorld", {"frameId": bframe["id"], "grantUniveralAccess": True})
        cid = ctx["executionContextId"]

        # Click audio button
        await cdp.send("Runtime.evaluate", {
            "expression": "document.querySelector('#recaptcha-audio-button')?.click()",
            "contextId": cid, "returnByValue": True,
        })
        await asyncio.sleep(3)

        # Get audio URL
        res = await cdp.send("Runtime.evaluate", {
            "expression": """
                (function() {
                    var l = document.querySelector('.rc-audiochallenge-tdownload-link');
                    if (l) return l.href;
                    var a = document.querySelector('audio source, audio');
                    if (a) return a.src;
                    return '';
                })()
            """,
            "contextId": cid, "returnByValue": True,
        })
        audio_url = res.get("result", {}).get("value", "")

        if not audio_url:
            await cdp.send("Runtime.evaluate", {
                "expression": "document.querySelector('#recaptcha-reload-button')?.click()",
                "contextId": cid, "returnByValue": True,
            })
            await asyncio.sleep(3)
            continue

        # Transcribe (bloqueante: se corre en un thread aparte para no frenar otras resoluciones)
        texto = await asyncio.to_thread(_transcribir_audio, audio_url)
        if not texto:
            await cdp.send("Runtime.evaluate", {
                "expression": "document.querySelector('#recaptcha-reload-button')?.click()",
                "contextId": cid, "returnByValue": True,
            })
            await asyncio.sleep(3)
            continue

        logger.info("Transcripción: '%s'", texto)

        # Submit answer
        await cdp.send("Runtime.evaluate", {
            "expression": f"document.querySelector('#audio-response').value = '{texto}'",
            "contextId": cid, "returnByValue": True,
        })
        await asyncio.sleep(0.5)
        await cdp.send("Runtime.evaluate", {
            "expression": "document.querySelector('#recaptcha-verify-button')?.click()",
            "contextId": cid, "returnByValue": True,
        })
        await asyncio.sleep(4)

        token = await cdp.evaluate("document.getElementById('g-recaptcha-response')?.value || ''")
        if token and len(token) > 10:
            return token

    return ""


def _transcribir_audio(url: str) -> str:
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            return ""

        mp3 = tempfile.mktemp(suffix=".mp3")
        wav = mp3.replace(".mp3", ".wav")
        with open(mp3, "wb") as f:
            f.write(resp.content)

        # MP3 → WAV con ffmpeg portable
        import imageio_ffmpeg
        subprocess.run([
            imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-i", mp3,
            "-ar", "16000", "-ac", "1", "-f", "wav", wav,
        ], capture_output=True, timeout=10)

        recognizer = sr.Recognizer()
        with sr.AudioFile(wav) as source:
            audio_data = recognizer.record(source)
        result = recognizer.recognize_google(audio_data, language="en-US")

        os.unlink(mp3)
        os.unlink(wav)
        return result

    except Exception as e:
        logger.warning("Error transcribiendo: %s", e)
        return ""


async def _solve(site_key: str, page_url: str) -> str:
    chrome_proc = None
    cdp_browser = None
    cdp_page = None
    user_data_dir = None
    port = _free_port()

    try:
        chrome_proc, user_data_dir = _launch_chrome(port)
        await asyncio.sleep(5)

        cdp_browser = CDPClient(await _get_ws_url(port))
        await cdp_browser.connect()

        result = await cdp_browser.send("Target.createTarget", {"url": page_url})
        target_id = result["targetId"]

        resp = await asyncio.to_thread(requests.get, f"http://localhost:{port}/json/list", timeout=5)
        page_ws = next((p["webSocketDebuggerUrl"] for p in resp.json() if p.get("id") == target_id), None)
        if not page_ws:
            raise Exception("No se pudo obtener WebSocket de la pestaña")

        cdp_page = CDPClient(page_ws)
        await cdp_page.connect()
        await cdp_page.send("Page.enable")
        await cdp_page.send("Runtime.enable")
        await asyncio.sleep(4)

        # Verificar reCAPTCHA
        if not await cdp_page.evaluate("!!document.querySelector('iframe[src*=\"recaptcha\"]')"):
            raise Exception("reCAPTCHA no cargó")

        # Click checkbox
        await _click_checkbox(cdp_page)
        await asyncio.sleep(3)

        # Verificar si pasó directo
        token = await cdp_page.evaluate("document.getElementById('g-recaptcha-response')?.value || ''")
        if token and len(token) > 10:
            return token

        # Resolver challenge de audio
        has_bframe = await cdp_page.evaluate("!!document.querySelector('iframe[src*=\"recaptcha/api2/bframe\"]')")
        if has_bframe:
            return await _resolver_audio(cdp_page)

        # Esperar token
        for _ in range(20):
            await asyncio.sleep(0.5)
            token = await cdp_page.evaluate("document.getElementById('g-recaptcha-response')?.value || ''")
            if token and len(token) > 10:
                return token

        return ""

    finally:
        for c in [cdp_page, cdp_browser]:
            if c:
                await c.close()
        if chrome_proc:
            chrome_proc.terminate()
            try:
                await asyncio.to_thread(chrome_proc.wait, timeout=5)
            except Exception:
                chrome_proc.kill()
        if user_data_dir:
            shutil.rmtree(user_data_dir, ignore_errors=True)


# ============================================================
# API pública
# ============================================================
async def resolver_recaptcha_v2(site_key: str, page_url: str, **kwargs) -> str:
    """Resuelve un reCAPTCHA v2. Awaitable y segura para correr concurrentemente:
    cada llamada lanza su propio Chrome en un puerto/perfil aislado, acotado por
    MAX_CONCURRENT_SOLVES resoluciones en simultáneo.

    Si una resolución falla (Chrome crashea, el reCAPTCHA no carga, se agotan
    los intentos de audio, etc.) reintenta con un Chrome nuevo hasta
    RECAPTCHA_RETRIES veces antes de propagar el error."""
    logger.info("Solving reCAPTCHA v2: %s", page_url)
    last_error = "no se pudo obtener el token"
    async with _SOLVE_SEMAPHORE:
        for intento in range(RECAPTCHA_RETRIES + 1):
            try:
                token = await _solve(site_key, page_url)
                if token:
                    return token
            except Exception as e:
                last_error = str(e)
            else:
                last_error = "no se pudo obtener el token"
            logger.warning("Intento %d/%d falló para %s: %s", intento + 1, RECAPTCHA_RETRIES + 1, page_url, last_error)
        raise RuntimeError(last_error)
