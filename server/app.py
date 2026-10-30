"""Local FastAPI entry point; blind assets are explicitly allowlisted."""
import os
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from server.config import ROOT
from server.blind.api import create_router

app = FastAPI(title='ZIVO Voice Support PoC')
from server.call_web import router as call_web_router; app.include_router(call_web_router)
from server.pipeline.api import router as call_router; app.include_router(call_router)
from server.blind.store import BlindStore
app.include_router(create_router(BlindStore(Path(os.environ.get('BLIND_DATA_DIR', ROOT/'data/blind')))))
BLIND_WEB = ROOT / 'web/dist-blind'

@app.middleware('http')
async def blind_no_cache(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith(('/blind','/api/blind')):
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
    return response

@app.get('/blind',response_class=HTMLResponse)
def blind():
    return (BLIND_WEB/'index.html').read_text()

@app.get('/blind/results',response_class=HTMLResponse)
def blind_results():
    return (BLIND_WEB/'results.html').read_text()

@app.get('/blind/assets/{asset}')
def blind_asset(asset: str):
    path = (BLIND_WEB/'assets'/asset).resolve()
    if not path.is_relative_to((BLIND_WEB/'assets').resolve()) or path.suffix not in {'.css','.js'} or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path)

@app.get('/report',response_class=HTMLResponse)
def report():
    from html import escape
    reports = sorted((ROOT/'reports').glob('bench-????????.md'))
    body = reports[-1].read_text() if reports else 'No benchmark report yet.'
    from server.pipeline.sessions import aggregate, render
    content=[render(aggregate())]
    in_table=False
    for line in body.splitlines():
        if line.startswith('|'):
            if line.startswith('|---'):
                continue
            cells=[escape(cell.strip()) for cell in line.strip('|').split('|')]
            if not in_table:
                content.append('<div class="scroll"><table><thead><tr>'+''.join('<th>'+cell+'</th>' for cell in cells)+'</tr></thead><tbody>')
                in_table=True
            else:
                content.append('<tr>'+''.join('<td>'+cell+'</td>' for cell in cells)+'</tr>')
            continue
        if in_table:
            content.append('</tbody></table></div>')
            in_table=False
        if line.startswith('# '):
            content.append('<h1>'+escape(line[2:])+'</h1>')
        elif line.startswith('## '):
            content.append('<h2>'+escape(line[3:])+'</h2>')
        elif line:
            content.append('<p>'+escape(line)+'</p>')
    if in_table:
        content.append('</tbody></table></div>')
    style='body{font:15px/1.6 system-ui;color:#182f35;background:#f3f5f1;margin:0;padding:32px}main{max-width:1800px;margin:auto}.scroll{overflow-x:auto;background:white;border:1px solid #ced9d2;border-radius:12px}table{border-collapse:collapse;min-width:1700px}th,td{padding:12px;text-align:left;border-bottom:1px solid #dce4dd;vertical-align:top}th{background:#e4eee8;font-size:13px}td{font-variant-numeric:tabular-nums}tr:nth-child(even){background:#f8faf7}p{max-width:1100px}'
    return '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TTS benchmark</title><style>'+style+'</style><main>'+''.join(content)+'</main>'


@app.get('/api/report/sessions')
def session_metrics():
    from server.pipeline.sessions import aggregate
    return aggregate()
