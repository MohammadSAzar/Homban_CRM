from django.utils.translation import gettext_lazy as _
from rest_framework import generics, mixins, status, viewsets
from rest_framework.exceptions import NotFound
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.user_serializers import StrictInputSerializer
from .models import Range
from .policies import RangePermission, range_details, roster_queryset, visible_ranges
from .serializers import MembershipWriteSerializer, RangeReadSerializer, RangeWriteSerializer, UserSummarySerializer
from .services import assign_consultant, remove_consultant, save_range


class RangePagination(PageNumberPagination):
    page_size = 50


def scoped_range(actor, pk):
    try:
        return visible_ranges(actor).get(pk=pk)
    except Range.DoesNotExist:
        raise NotFound(_("رنج یافت نشد.")) from None


class RangeViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [RangePermission]
    serializer_class = RangeReadSerializer
    pagination_class = RangePagination
    filter_backends = []
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        return range_details(self.request.user)

    def get_object(self):
        try:
            return self.get_queryset().get(pk=self.kwargs["pk"])
        except Range.DoesNotExist:
            raise NotFound(_("رنج یافت نشد.")) from None

    def _save(self, request, range_id=None):
        serializer = RangeWriteSerializer(data=request.data, partial=range_id is not None)
        serializer.is_valid(raise_exception=True)
        team = save_range(actor=request.user, data=serializer.validated_data, range_id=range_id)
        result = self.get_queryset().get(pk=team.pk)
        return Response(self.get_serializer(result).data,
                        status=status.HTTP_200_OK if range_id else status.HTTP_201_CREATED)

    def create(self, request):
        return self._save(request)

    def partial_update(self, request, pk=None):
        return self._save(request, self.get_object().pk)


class RangeConsultantListView(generics.ListAPIView):
    permission_classes = [RangePermission]
    serializer_class = UserSummarySerializer
    pagination_class = RangePagination
    filter_backends = []

    def get_queryset(self):
        team = scoped_range(self.request.user, self.kwargs["pk"])
        return roster_queryset(self.request.user, team)


class RangeMembershipView(APIView):
    permission_classes = [RangePermission]
    membership_write = True

    def put(self, request, pk, user_id):
        scoped_range(request.user, pk)
        serializer = MembershipWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        assign_consultant(actor=request.user, range_id=pk, user_id=user_id, data=serializer.validated_data)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def delete(self, request, pk, user_id):
        scoped_range(request.user, pk)
        serializer = StrictInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        remove_consultant(actor=request.user, range_id=pk, user_id=user_id, data=serializer.validated_data)
        return Response(status=status.HTTP_204_NO_CONTENT)
