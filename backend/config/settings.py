import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
RUNTIME = Path(os.environ.get("PNP_RUNTIME", ROOT / "runtime"))
RUNTIME.mkdir(mode=0o700, parents=True, exist_ok=True)
config = (
    json.loads((RUNTIME / "config.json").read_text())
    if (RUNTIME / "config.json").exists()
    else {}
)
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", config.get("secret_key", ""))
if not SECRET_KEY:
    raise RuntimeError("Run scripts/setup.py before starting the application.")
SERVICE_TOKEN = os.environ.get("PNP_SERVICE_TOKEN", config.get("service_token", ""))
DEBUG = False
ALLOWED_HOSTS = config.get("hosts", ["localhost", "127.0.0.1", "testserver"])
CSRF_TRUSTED_ORIGINS = [
    f"https://{h}:8443" for h in ALLOWED_HOSTS if h != "testserver"
] + [
    f"http://{h}:8000" for h in ALLOWED_HOSTS if h != "testserver"
]
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "rest_framework",
    "access",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "access.middleware.PrivateResponses",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = []
WSGI_APPLICATION = "config.wsgi.application"
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": config.get("database", "pnp"),
        "USER": config.get("db_user", "pnp"),
        "PASSWORD": config.get("db_password", ""),
        "HOST": "127.0.0.1",
        "PORT": config.get("db_port", 55432),
        "CONN_MAX_AGE": 30,
    }
}
if config.get("database_engine") == "sqlite3" or os.environ.get("PNP_DATABASE_SQLITE") == "1":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": RUNTIME / "db.sqlite3",
            "OPTIONS": {
                "timeout": 30,
            },
        }
    }
elif os.environ.get("PNP_TEST_SQLITE") == "1":
    DATABASES = {
        "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
    }
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
]
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Manila"
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
IS_HTTPS = config.get("https", True) and os.environ.get("PNP_INSECURE_HTTP") != "1"
SESSION_COOKIE_SECURE = IS_HTTPS
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = IS_HTTPS
SESSION_COOKIE_AGE = 8 * 3600
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
DATA_UPLOAD_MAX_MEMORY_SIZE = 3 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
MEDIA_ROOT = RUNTIME / "media"
STATIC_URL = "/static/"
STATIC_ROOT = RUNTIME / "static"
STATICFILES_DIRS = (
    [ROOT / "frontend" / "dist"] if (ROOT / "frontend" / "dist").exists() else []
)
REST_FRAMEWORK = {
    "EXCEPTION_HANDLER": "access.exceptions.handle",
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication"
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.AnonRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {"anon": "60/min"},
}

FILE_UPLOAD_PERMISSIONS = 0o600
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o700
