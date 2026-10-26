"""Public-demo access policy. Local development stays editable by default."""
import os
from fastapi import Request
from fastapi.responses import JSONResponse

def public_demo():
    return os.getenv('MLB_PUBLIC_DEMO','0')=='1'

async def protect_public_demo(request:Request,call_next):
    if public_demo() and request.method not in {'GET','HEAD','OPTIONS'}:
        return JSONResponse(status_code=403,content={'detail':'This public demo is read-only. Data imports and refresh jobs run only in the private local app.'})
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='strict-origin-when-cross-origin'
    response.headers['X-Frame-Options']='DENY'
    # Swagger/ReDoc load their own official CDN assets in the local workspace.
    if request.url.path not in {'/docs','/redoc','/docs/oauth2-redirect'}:
        response.headers['Content-Security-Policy']="default-src 'self'; img-src 'self' https://www.mlbstatic.com data:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    return response
