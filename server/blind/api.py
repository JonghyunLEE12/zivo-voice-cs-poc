"""Blind public API: allowlisted payloads only; never serve private paths or names."""
from typing import Literal
from fastapi import APIRouter, Cookie, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from server.blind.aggregate import aggregate, csv_export
from server.blind.store import BlindStore

Locale = Literal['en','zh-CN','zh-TW','ja','yue']
Native = Literal['en','zh-CN','zh-TW','ja','yue','ko','other']
Device = Literal['headphones','speakers']

class Profile(BaseModel):
    model_config = ConfigDict(extra='forbid')
    alias: str = Field(min_length=1,max_length=60,pattern=r'.*\S.*')
    native: Native
    device: Device

class Vote(BaseModel):
    model_config = ConfigDict(extra='forbid')
    assignment_id: str = Field(min_length=1,max_length=100)
    choice: Literal['A','same','B']
    pron_issue_a: bool = False
    pron_issue_b: bool = False
    listened_ms_a: float = Field(ge=0,le=600000,allow_inf_nan=False)
    listened_ms_b: float = Field(ge=0,le=600000,allow_inf_nan=False)
    comment: str = Field(default='',max_length=1000)


def create_router(store=None):
    store = store or BlindStore()
    router = APIRouter(prefix='/api/blind')

    @router.post('/session')
    def session(profile: Profile, response: Response):
        token = store.session(profile.alias,profile.native,profile.device)
        response.set_cookie('blind_session',token,httponly=True,samesite='strict',path='/api/blind')
        response.headers['Cache-Control'] = 'no-store'
        return {'ready':True}

    @router.get('/next')
    def next_pair(locale: Locale, blind_session: str | None = Cookie(default=None)):
        try:
            return store.next(blind_session,locale)
        except ValueError:
            raise HTTPException(401,'Start a listening session first')

    @router.get('/audio/{token}')
    def audio(token: str, blind_session: str | None = Cookie(default=None)):
        try:
            data = store.audio(blind_session,token)
        except ValueError:
            raise HTTPException(404,'Audio not found')
        # No FileResponse: no filename, modification time, ETag or engine headers.
        return Response(data,media_type='audio/wav',headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

    @router.post('/vote')
    def vote(payload: Vote, blind_session: str | None = Cookie(default=None)):
        try:
            return store.vote(blind_session,payload.model_dump())
        except ValueError as exc:
            raise HTTPException(400,str(exc))

    @router.get('/results')
    def results(locale: Locale | None = None, native: Native | None = None, device: Device | None = None,
                format: Literal['json','csv'] = 'json'):
        result = aggregate(store.votes(),locale=locale,native=native,device=device,labels=store.labels)
        if format=='csv':
            return Response(csv_export(result),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="blind-results.csv"'})
        return result
    return router
