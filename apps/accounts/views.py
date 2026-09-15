from rest_framework.generics import RetrieveAPIView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .serializers import (
    CurrentUserSerializer, CustomerLoginSerializer, CustomerRefreshSerializer,
)


class CustomerLoginView(TokenObtainPairView):
    serializer_class = CustomerLoginSerializer


class CustomerRefreshView(TokenRefreshView):
    serializer_class = CustomerRefreshSerializer


class CurrentUserView(RetrieveAPIView):
    serializer_class = CurrentUserSerializer

    def get_object(self):
        return self.request.user
