from rest_framework.routers import SimpleRouter
from .views import StagingViewSet

router = SimpleRouter()
router.register("staging", StagingViewSet, basename="staging")
urlpatterns = router.urls
