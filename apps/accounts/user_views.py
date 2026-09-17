from django.utils.translation import gettext_lazy as _
from rest_framework import generics, status
from rest_framework.exceptions import NotFound
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from .models import User
from .policies import CanManageOrganizationalUsers, user_details_queryset, visible_users
from .services import create_organizational_user, update_organizational_user
from .user_serializers import OrganizationalUserSerializer, UserCreateSerializer, UserUpdateSerializer


class UserPagination(PageNumberPagination):
    page_size = 50


class UserManagementMixin:
    permission_classes = [CanManageOrganizationalUsers]
    serializer_class = OrganizationalUserSerializer

    def get_queryset(self):
        actor = self.request.user
        return user_details_queryset(actor.workspace_id).filter(
            pk__in=visible_users(actor).values("pk"),
        ).order_by("username", "pk")

    def get_object(self):
        try:
            return self.get_queryset().get(pk=self.kwargs["pk"])
        except User.DoesNotExist:
            raise NotFound(_("کاربر یافت نشد.")) from None

    def user_response(self, user, response_status=status.HTTP_200_OK):
        result = user_details_queryset(self.request.user.workspace_id).get(pk=user.pk)
        return Response(OrganizationalUserSerializer(result).data, status=response_status)


class UserListCreateView(UserManagementMixin, generics.ListAPIView):
    pagination_class = UserPagination

    def post(self, request, *args, **kwargs):
        serializer = UserCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = create_organizational_user(actor=request.user, data=serializer.validated_data)
        return self.user_response(user, status.HTTP_201_CREATED)


class UserDetailView(UserManagementMixin, generics.RetrieveAPIView):
    # PATCH makes partial profile/status changes explicit; PUT and DELETE are not exposed.
    def patch(self, request, *args, **kwargs):
        target = self.get_object()
        serializer = UserUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = update_organizational_user(
            actor=request.user, user_id=target.pk, data=serializer.validated_data,
        )
        return self.user_response(user)
