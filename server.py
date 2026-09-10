"""
Servidor gRPC del servicio de CAPTCHA.
"""

import os
import time
import asyncio
import logging

import grpc
import captcha_pb2 as pb
import captcha_pb2_grpc as pb_grpc
import solver

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("captcha-server")


class CaptchaSolverServicer(pb_grpc.CaptchaSolverServicer):

    async def HealthCheck(self, request, context):
        return pb.HealthCheckResponse(ok=True, version="1.0.0")

    async def Solve(self, request, context):
        start = time.time()
        logger.info("Solve -> type=%s, %d bytes", pb.CaptchaType.Name(request.type), len(request.payload))
        token = await solver.resolver_recaptcha_v2(site_key="", page_url="")
        elapsed = int((time.time() - start) * 1000)
        return pb.SolveResponse(success=bool(token), token=token, elapsed=elapsed)

    async def SolveRecaptchaV2(self, request, context):
        logger.info("SolveRecaptchaV2 -> %s", request.site_key)
        token = await solver.resolver_recaptcha_v2(
            site_key=request.site_key,
            page_url=request.page_url,
        )
        return pb.SolveResponse(success=bool(token), token=token)


async def serve(host: str = "0.0.0.0", port: int = 50051):
    server = grpc.aio.server()
    pb_grpc.add_CaptchaSolverServicer_to_server(CaptchaSolverServicer(), server)
    server.add_insecure_port(f"{host}:{port}")
    await server.start()
    logger.info(
        "Servidor corriendo en %s:%d (async, hasta %s resoluciones simultáneas)",
        host, port, os.environ.get("MAX_CONCURRENT_SOLVES", "3"),
    )
    await server.wait_for_termination()


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 50051
    asyncio.run(serve(port=port))
