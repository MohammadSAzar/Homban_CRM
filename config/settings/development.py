from .base import *


DEBUG = True
CUSTOMER_ALLOW_WORKSPACE_HEADER = True

ALLOWED_HOSTS = [
    "127.0.0.1",
    "localhost",
]

DATABASES["default"]["TEST"] = {
    "NAME": "test_homban_db",
}
