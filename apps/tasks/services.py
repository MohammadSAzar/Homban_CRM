from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from apps.accounts.authentication import customer_can_authenticate
from apps.accounts.models import User
from apps.accounts.services import reject_extra_fields
from apps.organizations.models import Workspace
from common import jalali
from .models import ManualTask, _TASK_WRITE


def lock_actor(actor):
    """Workspace -> User, shared by task writes and the role-aware Daily Tasks read."""
    workspace = Workspace.objects.select_for_update().filter(pk=actor.workspace_id, is_active=True).first()
    user = User.objects.select_for_update().filter(pk=actor.pk, workspace=workspace).first() if workspace else None
    if user is None:
        raise PermissionDenied(_('دسترسی مجاز نیست.'))
    user.workspace = workspace
    if not customer_can_authenticate(user) or user.role not in User.Role.values:
        raise PermissionDenied(_('دسترسی مجاز نیست.'))
    return user


def owned_task(actor, pk, *, lock=False):
    rows = ManualTask.objects.for_owner(actor)
    if lock:
        rows = rows.select_for_update()
    row = rows.filter(pk=pk).first()
    if row is None:
        raise NotFound(_('کار یافت نشد.'))
    return row


def apply_schedule(row, data, *, creating=False):
    if not creating and not {'jalali_date', 'time', 'is_all_day'} & data.keys():
        return
    previous = {} if creating else jalali.format_datetime(row.scheduled_for, all_day=row.is_all_day)
    all_day = data.get('is_all_day', False if creating else row.is_all_day)
    date = data.get('jalali_date', previous.get('jalali_date'))
    clock = data.get('time', None if all_day else previous.get('time'))
    row.scheduled_for = jalali.schedule(date, clock, all_day=all_day)
    row.is_all_day = all_day


@transaction.atomic
def create_task(*, actor, data):
    actor = lock_actor(actor)
    reject_extra_fields(data, {'title','jalali_date','time','is_all_day'})
    row = ManualTask(workspace=actor.workspace, owner=actor, title=data['title'])
    apply_schedule(row, data, creating=True)
    row.save(_token=_TASK_WRITE)
    return row


@transaction.atomic
def update_task(*, actor, pk, data):
    actor = lock_actor(actor)
    reject_extra_fields(data, {'title','jalali_date','time','is_all_day'})
    row = owned_task(actor, pk, lock=True)
    apply_schedule(row, data)
    if 'title' in data:
        row.title = data['title']
    row.save(_token=_TASK_WRITE)
    return row


@transaction.atomic
def change_status(*, actor, pk, status):
    actor = lock_actor(actor)
    row = owned_task(actor, pk, lock=True)
    if status not in ManualTask.Status.values:
        raise ValidationError(_('وضعیت معتبر نیست.'))
    if status != row.status:
        row.status = status
        row.completed_at = timezone.now() if status == ManualTask.Status.DONE else None
        row.save(_token=_TASK_WRITE)
    return row


def is_overdue(row, now):
    if row.status != ManualTask.Status.PENDING:
        return False
    boundary = jalali.today_bounds(now)[0] if row.is_all_day else now
    return row.scheduled_for < boundary
