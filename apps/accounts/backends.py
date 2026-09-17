from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.core.exceptions import ValidationError


class WorkspaceIdentityBackend(ModelBackend):
    """Customer credentials require a workspace; internal staff use their UUID."""

    def authenticate(self, request, username=None, password=None, workspace=None, **kwargs):
        user_model = get_user_model()
        if password is None:
            return None
        users = user_model.objects.select_related("workspace")
        try:
            if workspace is not None:
                user = users.get(
                    workspace_id=workspace.pk, workspace__is_active=True, username=username,
                )
            else:
                identity = kwargs.get("id", username)
                if identity is None:
                    return None
                user = users.get(pk=identity, workspace__isnull=True, is_staff=True)
        except (user_model.DoesNotExist, ValidationError, ValueError, TypeError):
            # Match Django's dummy hashing for an unknown identity.
            user_model().set_password(password)
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
