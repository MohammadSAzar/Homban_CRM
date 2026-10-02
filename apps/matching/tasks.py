from celery import shared_task
from django.db import OperationalError
from .generation import process_work, recover_pending


@shared_task(autoretry_for=(OperationalError,), retry_backoff=True, retry_kwargs={"max_retries": 5})
def generate_recommendations(work_id, step=0):
    return process_work(work_id, step)


@shared_task
def recover_recommendations(after_id=0):
    # One bounded page per task, no continuous workspace scanning.
    cursor = recover_pending(after_id=after_id)
    if cursor is not None:
        recover_recommendations.delay(cursor)
