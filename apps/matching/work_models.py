from django.db import models
from django.utils.translation import gettext_lazy as _


class RecommendationWork(models.Model):
    """Transactional outbox + bounded continuation, not a result/history store."""
    workspace = models.ForeignKey("organizations.Workspace", on_delete=models.CASCADE, verbose_name=_("مجموعه"))
    kind = models.CharField(max_length=10, choices=[("file", _("فایل")), ("customer", _("مشتری")),
        ("profile", _("تنظیمات مشاور")), ("region", _("منطقه")), ("workspace", _("مجموعه"))], verbose_name=_("نوع کار"))
    target_id = models.UUIDField(null=True, verbose_name=_("شناسه هدف"))
    material = models.BooleanField(default=True, verbose_name=_("تغییر مؤثر"))
    phase = models.CharField(max_length=10, default="existing", verbose_name=_("مرحله"))
    after_id = models.UUIDField(null=True, verbose_name=_("نشانگر ادامه"))
    file_id = models.UUIDField(null=True, verbose_name=_("فایل جاری"))
    customer_after = models.UUIDField(null=True, verbose_name=_("نشانگر مشتری"))
    step = models.PositiveIntegerField(default=0, verbose_name=_("گام"))
    completed = models.BooleanField(default=False, verbose_name=_("پایان‌یافته"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("زمان ایجاد"))

    class Meta:
        indexes = [models.Index(fields=["completed", "id"], name="matching_pending_work_idx"),
                   models.Index(fields=["workspace", "kind", "target_id", "id"], name="matching_work_target_idx")]
        verbose_name = _("کار بازبینی پیشنهادها")
        verbose_name_plural = _("کارهای بازبینی پیشنهادها")
