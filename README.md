# Captcha Solver

Solver propio de reCAPTCHA v2 vía CDP (Chrome DevTools Protocol). Sin servicios externos.

## Requisitos

- Python 3.10+
- Google Chrome instalado

## Instalación

```bash
pip install -r requirements.txt
```

## Uso

### Paso 1: Prendé el servidor

```bash
python server.py
```

Dejalo corriendo en su propia terminal. Por default acepta hasta 3 resoluciones en paralelo; para cambiarlo:

```bash
# Windows (PowerShell)
$env:MAX_CONCURRENT_SOLVES = "5"
python server.py

# Linux/macOS
MAX_CONCURRENT_SOLVES=5 python server.py
```

## Estructura

```
captcha/
├── proto/captcha.proto     # Esquema Protobuf
├── captcha_pb2.py          # Stubs generados
├── captcha_pb2_grpc.py     # Stubs gRPC generados
├── server.py               # Servidor gRPC
├── solver.py               # Solver propio vía CDP
├── example.py              # Test
└── requirements.txt        # Dependencias
```

## Cómo funciona

1. `server.py` levanta un servidor gRPC asíncrono (`grpc.aio`)
2. `example.py` (u otro cliente) pide un token al servidor
3. `solver.py` lanza Chrome vía CDP en un puerto y perfil propios, hace click en el checkbox, resuelve el challenge de audio, y retorna el token
4. `example.py` envía el token al sitio

## Multi-request / concurrencia

El servidor y `solver.py` son asíncronos de punta a punta, así que aceptan varios clientes gRPC a la vez sin bloquearse entre sí:

- Cada `SolveRecaptchaV2` corre como una tarea de `asyncio` independiente, con su propio Chrome (puerto CDP y `--user-data-dir` generados dinámicamente), así que dos resoluciones concurrentes no compiten por el mismo puerto ni perfil.
- Un `asyncio.Semaphore` (`MAX_CONCURRENT_SOLVES`, default `3`) limita cuántos Chrome corren al mismo tiempo para no saturar la máquina; las requests que exceden el límite quedan en cola hasta que se libera un slot.
- El trabajo bloqueante (descarga/conversión de audio, reconocimiento de voz) se corre en threads (`asyncio.to_thread`) para no frenar al resto de las resoluciones en curso.
- Si una resolución falla (Chrome crashea, el reCAPTCHA no carga, se agotan los intentos de audio, etc.) se reintenta con un Chrome nuevo. Por default 1 reintento (2 intentos en total); se configura con `RECAPTCHA_RETRIES`. Si todos los intentos fallan, `SolveRecaptchaV2` devuelve `success=false` con el motivo en `error` (nunca tira una excepción de gRPC sin explicación).

Si necesitás probar varias resoluciones en paralelo desde Python, usá un stub async (`grpc.aio.insecure_channel`) y lanzá varios `SolveRecaptchaV2` con `asyncio.gather`; `example.py` sigue siendo síncrono (es solo un script de ejemplo/test de una sola resolución).

## Regenerar stubs Protobuf

Si modificás `proto/captcha.proto`:

```bash
python -m grpc_tools.protoc --python_out=. --grpc_python_out=. --proto_path=proto proto/captcha.proto
```

## Notas

- El solver resuelve reCAPTCHA v2 (checkbox + audio challenge)
- Usa Google Speech Recognition para transcribir el audio (gratis, sin API key)
- Chrome se lanza y cierra automáticamente en cada resolución, y su perfil temporal (`--user-data-dir`) se borra al terminar
- El cuello de botella real es CPU/RAM: cada resolución concurrente es un Chrome completo, así que subir `MAX_CONCURRENT_SOLVES` sin recursos de sobra va a ralentizar todo en vez de acelerarlo
