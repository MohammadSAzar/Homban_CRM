from django.core.exceptions import ValidationError as ModelValidationError
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from .policies import visible_staged_records, review_record
from .serializers import StagingListSerializer, StagingDetailSerializer, ReviewInput, EmptyInput
from .services import update_staged_record, mark_staged_record_ready, reject_staged_record
from .conversion import validate_for_conversion, convert_staged_record, issues_from_error


class StagingPagination(PageNumberPagination):
    page_size = 50


class StagingViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    pagination_class = StagingPagination
    http_method_names = ["get", "patch", "post", "head", "options"]
    filter_backends = []

    def get_queryset(self):
        return visible_staged_records(self.request.user).select_related("created_by").defer("raw_payload", "normalized_data")

    def get_serializer_class(self):
        return StagingListSerializer if self.action == "list" else StagingDetailSerializer

    def get_object(self):
        return review_record(self.request.user, self.kwargs["pk"])

    def _write(self, service, **kwargs):
        try:
            row = service(record_id=self.kwargs["pk"], actor=self.request.user, **kwargs)
        except ModelValidationError as error:
            raise ValidationError({"issues": issues_from_error(error)}) from error
        return Response(StagingDetailSerializer(row).data)

    def partial_update(self, request, pk=None):
        self.get_object()
        data = ReviewInput(data=request.data)
        data.is_valid(raise_exception=True)
        return self._write(update_staged_record, data=data.validated_data)

    def _empty(self, request):
        self.get_object()
        serializer = EmptyInput(data=request.data)
        serializer.is_valid(raise_exception=True)

    @action(detail=True, methods=["post"])
    def ready(self, request, pk=None):
        self._empty(request)
        return self._write(mark_staged_record_ready)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        self._empty(request)
        return self._write(reject_staged_record)

    @action(detail=True, methods=["post"])
    def validate(self, request, pk=None):
        self._empty(request)
        return Response(validate_for_conversion(record_id=pk, actor=request.user))

    @action(detail=True, methods=["post"])
    def convert(self, request, pk=None):
        self._empty(request)
        return self._write(convert_staged_record)


    @action(detail=True, methods=["get"])
    def duplicates(self, request, pk=None):
        from .duplicates.service import find_duplicate_candidates
        return Response(find_duplicate_candidates(record_id=pk, actor=request.user))
