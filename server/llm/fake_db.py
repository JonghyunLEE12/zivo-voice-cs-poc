"""Fictional demo bookings and deterministic Asia/Seoul policy arithmetic."""
from datetime import date, datetime, time, timedelta
from pathlib import Path
import json
import os
import unicodedata
from zoneinfo import ZoneInfo
from server.normalize.lexicon import display_name

DATA = Path(__file__).resolve().parents[2] / 'data'
SEOUL = ZoneInfo('Asia/Seoul')


class FakeDB:
    def __init__(self, today: str | None = None, now: datetime | str | None = None):
        self.rows = {r['reservation_code']:r for r in json.loads((DATA/'fake_reservations.json').read_text())}
        self.policies = json.loads((DATA/'policies.json').read_text())
        configured = now if now is not None else os.getenv('DEMO_NOW')
        if isinstance(configured, str):
            configured = datetime.fromisoformat(configured)
        if configured is not None and configured.tzinfo is None:
            raise ValueError('DEMO_NOW must be timezone-aware')
        self.now = configured.astimezone(SEOUL) if configured else None
        self.today = date.fromisoformat(today or os.getenv('DEMO_TODAY', self.now.date().isoformat() if self.now else self.policies['demo_today']))
        if self.now and self.now.date() != self.today:
            raise ValueError('DEMO_NOW date must match DEMO_TODAY/today')

    @staticmethod
    def surname_key(name: str) -> str:
        return unicodedata.normalize('NFKC',name).strip().casefold()

    def lookup(self, code: str, last_name: str, locale: str) -> dict:
        row = self.rows.get(code)
        if not row or self.surname_key(last_name) not in {self.surname_key(n) for n in row['last_name_aliases']}:
            # Same result for absent code and incorrect surname; do not leak booking data.
            return {'found':False,'error':'reservation_not_found','message':'Check the reservation code and guest surname.'}
        result = {k:v for k,v in row.items() if k != 'last_name_aliases'}
        result.update(found=True,name=display_name(row['lexicon_id'],locale))
        for role in ('origin','meeting'):
            if row.get(role+'_lexicon_id'):
                result[role] = {'name':display_name(row[role+'_lexicon_id'],locale),'lexicon_id':row[role+'_lexicon_id']}
        return result

    def cancellation_policy(self, code: str, locale: str, now: datetime | None = None) -> dict:
        row = self.rows[code]
        policy = self.policies[row['type']]
        now = now or self.now or datetime.combine(self.today, datetime.now(SEOUL).timetz())
        if now.tzinfo is None:
            raise ValueError('now must be timezone-aware')
        now = now.astimezone(SEOUL)
        deadlines = {}
        if policy['basis'] == 'elapsed_hours':
            start = datetime.fromisoformat(row['start_at']).astimezone(SEOUL)
            if now >= start:
                summaries = {
                    'en':'The service has already started, so this booking cannot be cancelled.',
                    'zh-CN':'服务已经开始，无法取消此预订。',
                    'zh-TW':'服務已經開始，無法取消此訂房。',
                    'ja':'サービス開始時刻を過ぎているため、キャンセルできません。',
                    'yue':'服務已經開始，唔可以取消呢個訂單。',
                }
                return {'reservation_code':code, 'cancellable':False, 'reason':'service_started',
                        'spoken_summary':summaries[locale], 'as_of':now.isoformat(), 'cancellation_executed':False}
            free_until = start - timedelta(hours=policy['free_hours_before'])
            fee = 0 if now <= free_until else policy['late_fee_percent']
            deadlines['free_until'] = free_until.isoformat()
        else:
            start = date.fromisoformat(row.get('check_in',row.get('start_at','')[:10]))
            free_until = start - timedelta(days=policy['free_days_before'])
            deadlines['free_until'] = free_until.isoformat()
            if now.date() <= free_until:
                fee = 0
            elif 'partial_days_before' in policy and now.date() <= start-timedelta(days=policy['partial_days_before']):
                fee = policy['partial_fee_percent']
            else:
                fee = policy['late_fee_percent']
            if 'partial_days_before' in policy:
                deadlines['partial_until'] = (start-timedelta(days=policy['partial_days_before'])).isoformat()
        fee_krw = row['total_krw'] * fee // 100
        refund_krw = row['total_krw']-fee_krw
        summaries = {
            'en':f'Today the cancellation fee is {fee}%, or ₩{fee_krw:,}, and the refund is ₩{refund_krw:,}.',
            'zh-CN':f'今天取消需支付{fee}%的费用，即{fee_krw:,}韩元，可退还{refund_krw:,}韩元。',
            'zh-TW':f'今天取消需支付{fee}%的手續費，也就是{fee_krw:,}韓元，可退還{refund_krw:,}韓元。',
            'ja':f'本日のキャンセル料は{fee}%で{fee_krw:,}ウォン、返金額は{refund_krw:,}ウォンです。',
            'yue':f'今日取消手續費係{fee}%，即係{fee_krw:,}韓元，可以退返{refund_krw:,}韓元。',
        }
        return {'cancellable':True,'reservation_code':code,'name':display_name(row['lexicon_id'],locale),'spoken_summary':summaries[locale],
                'as_of':now.isoformat(),'timezone':'Asia/Seoul','basis':policy['basis'],
                'total_krw':row['total_krw'],'fee_percent':fee,'refund_percent':100-fee,
                'cancellation_fee_krw':fee_krw,'refund_krw':row['total_krw']-fee_krw,
                **deadlines,'deadline_inclusive':True,'policy':dict(policy),'cancellation_executed':False}
