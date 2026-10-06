"""Shared Jalali input/display boundary; canonical values are for internal services only."""
import re
from datetime import datetime, time, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

import jdatetime
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

MONTHS = ('فروردین', 'اردیبهشت', 'خرداد', 'تیر', 'مرداد', 'شهریور',
          'مهر', 'آبان', 'آذر', 'دی', 'بهمن', 'اسفند')
WEEKDAYS = ('دوشنبه', 'سه‌شنبه', 'چهارشنبه', 'پنجشنبه', 'جمعه', 'شنبه', 'یکشنبه')
TO_ASCII = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')
TO_PERSIAN = str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')


def business_timezone():
    return ZoneInfo(settings.TIME_ZONE)


def parse_date(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}/[0-9]{2}/[0-9]{2}', value.translate(TO_ASCII)):
        raise ValidationError(_('تاریخ شمسی باید به صورت سال/ماه/روز مانند ۱۴۰۵/۰۷/۱۵ باشد.'))
    try:
        day = jdatetime.date(*(int(part) for part in value.translate(TO_ASCII).split('/')))
        day.togregorian()  # Ensure the canonical datetime representation is supported too.
        return day
    except (ValueError, OverflowError):
        raise ValidationError(_('روز، ماه یا سال شمسی معتبر نیست.')) from None


def parse_time(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{2}:[0-9]{2}', value.translate(TO_ASCII)):
        raise ValidationError(_('ساعت باید به صورت ۲۴ ساعته مانند ۱۰:۳۰ باشد.'))
    try:
        return time(*(int(part) for part in value.translate(TO_ASCII).split(':')))
    except ValueError:
        raise ValidationError(_('ساعت معتبر نیست.')) from None


def localize(day, clock):
    naive = datetime.combine(day, clock)
    zone = business_timezone()
    candidates = {naive.replace(tzinfo=zone, fold=fold).astimezone(dt_timezone.utc)
                  for fold in (0, 1)
                  if naive.replace(tzinfo=zone, fold=fold).astimezone(dt_timezone.utc).astimezone(zone).replace(tzinfo=None) == naive}
    if len(candidates) != 1:
        raise ValidationError(_('این ساعت محلی به علت تغییر ساعت رسمی ناموجود یا مبهم است؛ ساعت دیگری انتخاب کنید.'))
    return candidates.pop()


def day_start(day):
    """Earliest real instant of a local day, including historical midnight DST gaps."""
    zone = business_timezone()
    midnight = datetime.combine(day, time.min)
    candidates = [midnight.replace(tzinfo=zone, fold=f).astimezone(dt_timezone.utc) for f in (0, 1)]
    valid = [instant for instant in candidates if instant.astimezone(zone).date() == day]
    return min(valid)


def schedule(date_value, time_value=None, *, all_day=False):
    day = parse_date(date_value).togregorian()
    if all_day:
        if time_value is not None:
            raise ValidationError(_('برای کار تمام‌روز نباید ساعت تعیین شود.'))
        return day_start(day)
    return localize(day, parse_time(time_value))


def local_now(now=None):
    return timezone.localtime(now or timezone.now(), business_timezone())


def today_bounds(now=None):
    day = local_now(now).date()
    return day_start(day), day_start(day + timedelta(days=1))


def range_bounds(start, end, *, max_days=366):
    first, last = parse_date(start), parse_date(end)
    days = (last - first).days + 1
    if days < 1:
        raise ValidationError(_('ابتدای بازه نباید پس از انتهای آن باشد.'))
    if max_days is not None and days > max_days:
        raise ValidationError(_('بازه تقویم باید شامل حداکثر ۳۶۶ روز شمسی باشد.'))
    return day_start(first.togregorian()), day_start(last.togregorian() + timedelta(days=1))


def format_datetime(value, *, all_day=False):
    if timezone.is_naive(value):
        raise ValidationError(_('زمان باید دارای منطقه زمانی باشد.'))
    local = timezone.localtime(value, business_timezone())
    day = jdatetime.date.fromgregorian(date=local.date())
    weekday, month = WEEKDAYS[local.weekday()], MONTHS[day.month - 1]
    return {'jalali_date': f'{day.year:04}/{day.month:02}/{day.day:02}',
            'date_display': f'{weekday} {day.day} {month} {day.year}'.translate(TO_PERSIAN),
            'weekday': weekday, 'month_name': month,
            'time': None if all_day else f'{local.hour:02}:{local.minute:02}'}


def timestamp_display(value):
    if value is None:
        return None
    fields = format_datetime(value)
    return f"{fields['date_display']} ساعت {fields['time'].translate(TO_PERSIAN)}"
