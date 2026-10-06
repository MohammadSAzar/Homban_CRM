import uuid
from datetime import datetime, timezone as utc
from unittest.mock import patch
import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models.deletion import ProtectedError
from common import jalali
from apps.tasks.models import ManualTask, _TASK_WRITE
from apps.tasks.services import create_task, update_task, change_status, is_overdue

pytestmark = pytest.mark.django_db


def make(actor, **changes):
    return create_task(actor=actor, data={'title':'تماس با مشتری', 'jalali_date':'1405/07/15','time':'10:30',**changes})


def test_schema_and_indexes(people):
    row = make(people.owner)
    row.refresh_from_db()
    assert isinstance(row.pk,uuid.UUID) and row.workspace_id == people.workspace.pk and row.owner_id == people.owner.pk
    assert row.scheduled_for.tzinfo and row.completed_at is None and row.status == 'pending'
    assert {tuple(index.fields) for index in ManualTask._meta.indexes} == {
        ('workspace','owner','status','scheduled_for'),('workspace','owner','scheduled_for')}
    assert not {'jalali_date','reminder','customer','property_file'} & {f.name for f in ManualTask._meta.fields}


@pytest.mark.parametrize('start', ['pending','done','cancelled'])
@pytest.mark.parametrize('target', ['pending','done','cancelled'])
def test_all_transitions_idempotent(people, start, target):
    row = make(people.owner)
    first = datetime(2026,10,7,7,tzinfo=utc.utc)
    later = datetime(2026,10,7,8,tzinfo=utc.utc)
    with patch('django.utils.timezone.now',return_value=first):
        row = change_status(actor=people.owner,pk=row.pk,status=start)
    with patch('django.utils.timezone.now',return_value=later):
        row = change_status(actor=people.owner,pk=row.pk,status=target)
    assert row.completed_at == (first if start == target == 'done' else later if target == 'done' else None)
    modified = row.updated_at
    row = change_status(actor=people.owner,pk=row.pk,status=target)
    assert row.updated_at == modified


@pytest.mark.parametrize('mutation', ['foreign','owner','workspace','title','naive','completion','midday'])
def test_model_invariants(people, mutation):
    row = make(people.owner)
    if mutation == 'foreign': row.owner=people.foreign
    elif mutation == 'owner': row.owner=people.other
    elif mutation == 'workspace': row.workspace=people.foreign_workspace
    elif mutation == 'title': row.title=' '
    elif mutation == 'naive': row.scheduled_for=datetime(2026,10,7)
    elif mutation == 'completion': row.status='done'
    else: row.is_all_day=True
    with pytest.raises(ValidationError): row.save(_token=_TASK_WRITE)


@pytest.mark.parametrize('operation', ['save','partial','update','bulk','delete','row_delete'])
def test_normal_write_bypasses_guarded(people, operation):
    row = make(people.owner)
    with pytest.raises(ValidationError):
        if operation == 'save': row.save()
        elif operation == 'partial': row.save(_token=_TASK_WRITE,update_fields=['status'])
        elif operation == 'update': ManualTask.objects.filter(pk=row.pk).update(status='done')
        elif operation == 'bulk': ManualTask.objects.bulk_create([row])
        elif operation == 'delete': ManualTask.objects.all().delete()
        else: row.delete()


@pytest.mark.parametrize('values', [{'status':'new'},{'status':'done'}, {'completed_at':datetime(2026,10,7,tzinfo=utc.utc)}])
def test_database_constraints(people, values):
    row = make(people.owner)
    with pytest.raises(IntegrityError), transaction.atomic():
        models.QuerySet(model=ManualTask).filter(pk=row.pk).update(**values)


def test_history_protected(people):
    make(people.owner)
    with pytest.raises(ProtectedError): people.owner.delete()
    with pytest.raises(ProtectedError): people.workspace.delete()


def test_all_day_and_timed_overdue(people):
    timed = make(people.owner)
    day = create_task(actor=people.owner,data={'title':'روز کامل','jalali_date':'1405/07/15','is_all_day':True})
    now = jalali.schedule('1405/07/15','11:00')
    assert is_overdue(timed,now) and not is_overdue(day,now)
    assert not is_overdue(timed,timed.scheduled_for)
    assert is_overdue(day,jalali.schedule('1405/07/16','00:00'))
    day = change_status(actor=people.owner,pk=day.pk,status='done')
    assert not is_overdue(day,now)


def test_failed_edit_is_atomic_and_schedule_transitions(people):
    row = make(people.owner)
    with pytest.raises(ValidationError):
        update_task(actor=people.owner,pk=row.pk,data={'title':'تغییر','time':'24:00'})
    row.refresh_from_db(); assert row.title == 'تماس با مشتری'
    row = update_task(actor=people.owner,pk=row.pk,data={'is_all_day':True})
    assert row.is_all_day and jalali.format_datetime(row.scheduled_for)['time'] == '00:00'
    with pytest.raises(ValidationError): update_task(actor=people.owner,pk=row.pk,data={'is_all_day':False})
    row = update_task(actor=people.owner,pk=row.pk,data={'is_all_day':False,'time':'18:30'})
    assert jalali.format_datetime(row.scheduled_for)['time'] == '18:30'
    row = update_task(actor=people.owner,pk=row.pk,data={'jalali_date':'1405/07/16'})
    assert jalali.format_datetime(row.scheduled_for)['time'] == '18:30'


def test_service_rechecks_current_actor_and_rejects_protected_data(people):
    from rest_framework.exceptions import PermissionDenied, ValidationError as ApiValidationError
    from apps.accounts.models import User
    with pytest.raises(ApiValidationError):
        make(people.owner,status='done')
    models.QuerySet(model=User).filter(pk=people.owner.pk).update(is_active=False)
    with pytest.raises(PermissionDenied): make(people.owner)
    models.QuerySet(model=User).filter(pk=people.owner.pk).update(is_active=True)
    people.workspace.is_active=False; people.workspace.save()
    with pytest.raises(PermissionDenied): make(people.owner)


def test_invalid_status_service(people):
    from rest_framework.exceptions import ValidationError as ApiValidationError
    row=make(people.owner)
    with pytest.raises(ApiValidationError): change_status(actor=people.owner,pk=row.pk,status='seen')
