import requests
import grpc
import captcha_pb2 as pb
import captcha_pb2_grpc as pb_grpc

# ============================================================
# Config - cambia si tu servidor está en otro lado
# ============================================================
GRPC_ADDRESS = "localhost:50051"
DEMO_URL = "https://www.google.com/recaptcha/api2/demo"
SITE_KEY = "6Le-wvkSAAAAAPBMRTvw0Q4Muexq9bi0DJwx_mJ-"
# ============================================================
# 1. Pedir token al servicio gRPC
# ============================================================
channel = grpc.insecure_channel(GRPC_ADDRESS)
grpc.channel_ready_future(channel).result(timeout=5)
stub = pb_grpc.CaptchaSolverStub(channel)

print(f"[*] Pidiendo token para site_key={SITE_KEY}")
response = stub.SolveRecaptchaV2(pb.RecaptchaV2Request(
    site_key=SITE_KEY,
    page_url=DEMO_URL,
))

if not response.success:
    print(f"[!] Error al resolver: {response.error}")
    exit(1)

token = response.token
print(f"[+] Token obtenido: {token[:60]}...")

# ============================================================
# 2. Enviar el form con el token 
# ============================================================
headers = {
    'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
    'accept-language': 'es-US,es-419;q=0.9,es;q=0.8,en-US;q=0.7,en;q=0.6',
    'available-dictionary': ':Z6I3IkzLw2JIIkJkb5aMuKFxOfrp5NZbvZIGc7QJMak=:',
    'cache-control': 'max-age=0',
    'content-type': 'application/x-www-form-urlencoded',
    'dnt': '1',
    'downlink': '10',
    'origin': 'https://www.google.com',
    'priority': 'u=0, i',
    'referer': 'https://www.google.com/recaptcha/api2/demo',
    'rtt': '50',
    'sec-ch-prefers-color-scheme': 'dark',
    'sec-ch-ua': '"Chromium";v="152", "Not?A_Brand";v="24", "Google Chrome";v="152"',
    'sec-ch-ua-arch': '"x86"',
    'sec-ch-ua-bitness': '"64"',
    'sec-ch-ua-form-factors': '"Desktop"',
    'sec-ch-ua-full-version': '"152.0.7977.83"',
    'sec-ch-ua-full-version-list': '"Chromium";v="152.0.7977.83", "Not?A_Brand";v="24.0.0.0", "Google Chrome";v="152.0.7977.83"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-model': '""',
    'sec-ch-ua-platform': '"Windows"',
    'sec-ch-ua-platform-version': '"10.0.0"',
    'sec-ch-ua-wow64': '?0',
    'sec-fetch-dest': 'document',
    'sec-fetch-mode': 'navigate',
    'sec-fetch-site': 'same-origin',
    'sec-fetch-user': '?1',
    'upgrade-insecure-requests': '1',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36',
    'x-browser-channel': 'stable',
    'x-browser-copyright': 'Copyright 2026 Google LLC. All Rights Reserved.',
    'x-browser-validation': 'vgyhv7tDQEn+LFULEOyfsn7nRqI=',
    'x-browser-year': '2026',
    'x-client-data': 'CKmdygEIlaHLAQiFoM0BCNLflDAIp+OUMAiD5ZQwCJXnlDAYp96UMBis35QwGLLglDA=',
}

data = {
    'g-recaptcha-response': token,
}

response = requests.post(DEMO_URL, headers=headers, data=data)


if "Se verificó correctamente... ¡Hip, hip, hurra!" in response.text:
    print("[+] Captcha resuelto correctamente")
else:
    print("[!] Error al enviar el form")