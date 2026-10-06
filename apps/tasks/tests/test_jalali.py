from datetime import datetime, date, time, timezone
import pytest
from django.core.exceptions import ValidationError
from common import jalali


@pytest.mark.parametrize('value', ['1405/07/15','۱۴۰۵/۰۷/۱۵','١٤٠٥/٠٧/١٥','1405/01/31','1405/06/31','1405/12/29','1403/12/30','1404/01/01'])
def test_valid_dates(value):
    parsed = jalali.parse_date(value)
    assert jalali.format_datetime(jalali.schedule(value, all_day=True))['jalali_date'] == f'{parsed.year:04}/{parsed.month:02}/{parsed.day:02}'


@pytest.mark.parametrize('value', ['1405/00/01','1405/13/01','1405/01/00','1405/07/31','1405/12/30',
    '1404/12/30','1405/01/32','15/07/1405','1405-07-15','2026-10-07','1405/7/15','bad',None,14050715,'9999/01/01'])
def test_invalid_dates(value):
    with pytest.raises(ValidationError):
        jalali.parse_date(value)


@pytest.mark.parametrize('clock', ['00:00','23:59','۱۰:۳۰'])
def test_clock(clock):
    value = jalali.schedule('1405/07/15',clock)
    assert value.tzinfo is not None
    assert jalali.format_datetime(value)['time'] == clock.translate(jalali.TO_ASCII)


@pytest.mark.parametrize('clock', ['24:00','23:60','9:00','12:00 PM',None,'bad'])
def test_bad_clock(clock):
    with pytest.raises(ValidationError):
        jalali.schedule('1405/07/15',clock)


def test_exact_display_and_canonical_conversion():
    value = jalali.schedule('1405/07/15','10:30')
    assert value == datetime(2026,10,7,7,0,tzinfo=timezone.utc)
    assert jalali.format_datetime(value) == {'jalali_date':'1405/07/15', 'date_display':'چهارشنبه ۱۵ مهر ۱۴۰۵',
        'weekday':'چهارشنبه','month_name':'مهر','time':'10:30'}
    assert jalali.timestamp_display(value) == 'چهارشنبه ۱۵ مهر ۱۴۰۵ ساعت ۱۰:۳۰'
    assert jalali.timestamp_display(None) is None


def test_tehran_day_is_not_utc_day():
    now = datetime(2026,10,6,21,0,tzinfo=timezone.utc)
    assert jalali.format_datetime(now)['jalali_date'] == '1405/07/15'
    start,end = jalali.today_bounds(now)
    assert start == datetime(2026,10,6,20,30,tzinfo=timezone.utc)
    assert end == datetime(2026,10,7,20,30,tzinfo=timezone.utc)


def test_leap_range_and_limit():
    start,end = jalali.range_bounds('1399/01/01','1399/12/30')
    assert (end-start).days == 366
    with pytest.raises(ValidationError):
        jalali.range_bounds('1399/01/01','1400/01/01')
    with pytest.raises(ValidationError):
        jalali.range_bounds('1405/02/01','1405/01/01')


def test_historical_dst_gap_and_naive_rejection():
    with pytest.raises(ValidationError):
        jalali.localize(date(2021,3,22),time(0,30))
    start = jalali.day_start(date(2021,3,22))
    assert jalali.local_now(start).date() == date(2021,3,22)
    assert jalali.local_now(start).hour == 1
    with pytest.raises(ValidationError):
        jalali.format_datetime(datetime(2026,1,1))
    with pytest.raises(ValidationError):
        jalali.schedule('1405/07/15','10:30',all_day=True)
