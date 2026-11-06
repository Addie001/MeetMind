"""Shared HTTP configuration; business routes live in server.py."""
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware


def create_app(lifespan):
    app = FastAPI(title='MeetMind', lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost'])

    @app.middleware('http')
    async def request_limits(request: Request, call_next):
        if request.method == 'POST':
            try:
                length = int(request.headers.get('content-length', 0))
            except ValueError:
                length = 0
            if not 0 < length <= 2_000_000:
                return JSONResponse({'error': '请求为空或超过 2MB'}, status_code=400)
            # Check actual bytes too, including requests with misleading Content-Length.
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 2_000_000:
                    return JSONResponse({'error': '请求为空或超过 2MB'}, status_code=400)
            request._body = bytes(body)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        if request.url.path in ('/', '/app.js', '/style.css'):
            response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'"
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return JSONResponse({'error': exc.detail}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({'error': '请求参数不正确'}, status_code=400)


    async def business_error(request, exc):
        message = str(exc) if isinstance(exc, ValueError) else '请求参数不正确'
        return JSONResponse({'error': message}, status_code=400)


    for error_type in (ValueError, KeyError, TypeError, StopIteration):
        app.add_exception_handler(error_type, business_error)

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        return JSONResponse({'error': '操作失败，请检查服务配置'}, status_code=500)


    return app
