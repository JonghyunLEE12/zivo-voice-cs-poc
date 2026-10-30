"""Allowlisted Vite call assets; model runtimes are never loaded by HTTP routes."""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from server.config import ROOT

router = APIRouter()
WEB = ROOT / 'web/dist'

@router.get('/')
def call_page():
    if not (WEB / 'index.html').is_file():
        raise HTTPException(503, 'Run pnpm --dir web build first')
    return FileResponse(WEB / 'index.html', headers={'Cache-Control': 'no-cache'})

def asset_file(folder: str, name: str, suffixes: set[str]):
    base = (WEB / folder).resolve()
    path = (base / name).resolve()
    if not path.is_relative_to(base) or path.suffix not in suffixes or not path.is_file():
        raise HTTPException(404)
    media = {'.wasm': 'application/wasm', '.mjs': 'text/javascript', '.onnx': 'application/octet-stream'}.get(path.suffix)
    return FileResponse(path, media_type=media, headers={'X-Content-Type-Options': 'nosniff'})

@router.get('/assets/{asset}')
def call_asset(asset: str):
    return asset_file('assets', asset, {'.js', '.css', '.wasm', '.mjs'})

@router.get('/vad/{asset}')
def vad_asset(asset: str):
    return asset_file('vad', asset, {'.js', '.mjs', '.onnx', '.wasm'})
