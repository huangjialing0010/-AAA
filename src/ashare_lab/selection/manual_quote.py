"""Strict price normalization only; no inference of buyability."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

SHANGHAI = timezone(timedelta(hours=8))


def normalize_quote(payload, *, code, day, observed_at):
    if observed_at.tzinfo is None or observed_at.astimezone(SHANGHAI).date() != day:
        raise ValueError('OBSERVATION_DATE_MISMATCH')
    data = payload.get('data')
    if payload.get('rc') != 0 or not isinstance(data, dict) or data.get('f57') != code:
        raise ValueError('QUOTE_IDENTITY_MISMATCH')
    if type(data.get('f59')) is not int or data['f59'] != 2:
        raise ValueError('UNVERIFIED_PRICE_SCALE')
    if type(data.get('f86')) is not int:
        raise ValueError('MISSING_QUOTE_TIME')
    instant = datetime.fromtimestamp(data['f86'], SHANGHAI)
    if instant.date() != day or instant > observed_at:
        raise ValueError('QUOTE_DATE_MISMATCH')
    prices = {}
    for field, key in [('f46','open'),('f44','high'),('f45','low'),('f43','close'),
                       ('f51','upper'),('f52','lower'),('f60','previous_close')]:
        value = data.get(field)
        if type(value) is not int or value <= 0:
            raise ValueError('MISSING_OR_INVALID_PRICE:'+field)
        prices[key] = D(value)/100
    if not prices['lower'] <= prices['low'] <= prices['open'] <= prices['high'] <= prices['upper']:
        raise ValueError('INCONSISTENT_PRICE_RANGE')
    if not prices['low'] <= prices['close'] <= prices['high']:
        raise ValueError('INCONSISTENT_CLOSE')
    return {**prices, 'quote_time':instant, 'code':code, 'tradable':None}
