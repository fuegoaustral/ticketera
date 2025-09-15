import os
from pathlib import Path

import django
from django.utils.encoding import smart_str

django.utils.encoding.smart_text = smart_str

import django.utils.translation as original_translation
from django.utils.translation import gettext_lazy

original_translation.ugettext_lazy = gettext_lazy

from django.utils.encoding import force_str

django.utils.encoding.force_text = force_str

from dotenv import load_dotenv

load_dotenv()  # take environment variables

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/3.2/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = 'django-insecure-s$(*=)6^h$p=d6e4tpv#-s7_hg&cl!vc@yzas371ubj=+ks&cc'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.environ.get('DEBUG', 'True') == 'True'
print(f'DEBUG: {DEBUG}')

ENV = os.environ.get('ENV', 'local')
print(f'ENV: {ENV}')
ALLOWED_HOSTS = [
    '127.0.0.1',
    'localhost',
    'bonos.fa2022.org',
    'eventos.fuegoaustral.org',
    os.environ.get('EXTRA_HOST')
]
print(f'ALLOWED_HOSTS: {ALLOWED_HOSTS}')

APP_URL = os.environ.get('APP_URL', 'http://localhost:8000')
print(f'APP_URL: {APP_URL}')

# Application definition
AUTHENTICATION_BACKENDS = [
    # Needed to login by username in Django admin, regardless of `allauth`
    'django.contrib.auth.backends.ModelBackend',

    # `allauth` specific authentication methods, such as login by email
    'allauth.account.auth_backends.AuthenticationBackend',

]

INSTALLED_APPS = [
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.humanize',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.admin',
    'bootstrap5',
    'django_inlinecss',
    'django_s3_storage',
    'auditlog',
    'allauth',
    'allauth.account',

    'allauth.socialaccount',
    'allauth.socialaccount.providers.google',
    'import_export',

    'user_profile.apps.UserProfileConfig',

    'tickets.apps.TicketsConfig',
    'events.apps.EventsConfig',
]

MIDDLEWARE = [
    'utils.loggerMiddleware.LoggerMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'auditlog.middleware.AuditlogMiddleware',
    "allauth.account.middleware.AccountMiddleware",
    'tickets.middleware.ProfileCompletionMiddleware',
    'tickets.middleware.DeviceDetectionMiddleware'

]

ROOT_URLCONF = 'deprepagos.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [
            os.path.join(BASE_DIR, 'tickets/templates'),
            os.path.join(BASE_DIR, 'user_profile/templates'),

        ],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'utils.context_processors.current_event',
                'utils.context_processors.app_url',
                'utils.context_processors.chatwoot_token',
                'utils.context_processors.env',
                'utils.context_processors.chatwoot_identifier_hash',
            ],
        },
    },
]

WSGI_APPLICATION = 'deprepagos.wsgi.application'

# Database
# https://docs.djangoproject.com/en/3.2/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('DB_DATABASE', 'deprepagos'),
        'USER': os.environ.get('DB_USER', 'mauro'),
        'PASSWORD': os.environ.get('DB_PASSWORD', ''),
        'HOST': os.environ.get('DB_HOST', '127.0.0.1'),
        'PORT': os.environ.get('DB_PORT', '5432'),
    }
}
print(f'DATABASE HOST: {DATABASES["default"]["HOST"]}')

# Password validation
# https://docs.djangoproject.com/en/3.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Internationalization
# https://docs.djangoproject.com/en/3.2/topics/i18n/

LANGUAGE_CODE = 'es-AR'

TIME_ZONE = 'America/Argentina/Buenos_Aires'

USE_I18N = True

USE_L10N = True

USE_TZ = True

# Default primary key field type
# https://docs.djangoproject.com/en/3.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/4.2/howto/static-files/

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(PROJECT_ROOT, 'static')

MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(PROJECT_ROOT, 'media')

STATICFILES_FINDERS = (
    'django.contrib.staticfiles.finders.FileSystemFinder',
    'django.contrib.staticfiles.finders.AppDirectoriesFinder',
)

MERCADOPAGO = {
    'PUBLIC_KEY': os.environ.get('MERCADOPAGO_PUBLIC_KEY'),
    'ACCESS_TOKEN': os.environ.get('MERCADOPAGO_ACCESS_TOKEN'),
    'WEBHOOK_SECRET': os.environ.get('MERCADOPAGO_WEBHOOK_SECRET')
}

EMAIL_HOST = os.environ.get('EMAIL_HOST')
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD')
EMAIL_PORT = os.environ.get('EMAIL_PORT')
EMAIL_USE_TLS = True

DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'Fuego Austral <bonos@eventos.fuegoaustral.org>').replace('"',
                                                                                                                    '').replace(
    "'", '')

TEMPLATED_EMAIL_TEMPLATE_DIR = 'emails/'
TEMPLATED_EMAIL_FILE_EXTENSION = 'html'

TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID', '')
TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN', '')
TWILIO_VERIFY_SERVICE_SID = os.environ.get('TWILIO_VERIFY_SERVICE_SID', '')

SOCIALACCOUNT_PROVIDERS = {
    'google': {
        'SCOPE': [
            'email',
        ],
        'APP': {
            'client_id': os.environ.get('GOOGLE_CLIENT_ID', ''),
            'secret': os.environ.get('GOOGLE_SECRET', '')
        }
    }
}

try:
    from deprepagos.local_settings import *

    INSTALLED_APPS.extend(EXTRA_INSTALLED_APPS)
except ImportError:
    # using print and not log here as logging is yet not configured
    print('local settings not found')
    pass

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'DEBUG',
    },
    'loggers': {
        'botocore': {
            'level': 'ERROR',  # Change to ERROR to reduce logs
            'handlers': ['console'],
            'propagate': False,
        },
        's3transfer': {
            'level': 'ERROR',  # Change to ERROR to reduce logs
            'handlers': ['console'],
            'propagate': False,
        },
        '': {
            'handlers': ['console'],
            'level': os.getenv('DJANGO_LOG_LEVEL', 'DEBUG'),
            'propagate': False,
        }
    },
}

# ENABLE DEBUG LOGGING FOR DATABASE QUERIES
# if ENV == 'local':
#     LOGGING['loggers']['django.db'] = {
#         'level': 'DEBUG'
#     }


AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {
            "min_length": 8,
        },
    }
]

# Email settings
ACCOUNT_EMAIL_REQUIRED = True
ACCOUNT_EMAIL_VERIFICATION = 'mandatory'
ACCOUNT_USERNAME_REQUIRED = False
ACCOUNT_USER_MODEL_USERNAME_FIELD = None

# Login settings
LOGIN_REDIRECT_URL = 'mi_fuego'
LOGIN_URL = '/mi-fuego/login/'
ACCOUNT_LOGOUT_REDIRECT_URL = APP_URL
ACCOUNT_AUTHENTICATION_METHOD = 'email'  # 'username_email', 'username'
ACCOUNT_EMAIL_CONFIRMATION_EXPIRE_DAYS = 3

SOCIALACCOUNT_EMAIL_AUTHENTICATION = True
SOCIALACCOUNT_LOGIN_ON_GET = True
ACCOUNT_LOGOUT_ON_GET = True
ACCOUNT_CONFIRM_EMAIL_ON_GET = True
ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION = True

# Disable automatic messages for login/logout
ACCOUNT_MESSAGE_TEMPLATE = None
ACCOUNT_DEFAULT_HTTP_PROTOCOL = (
    "http" if "localhost" in APP_URL or "127.0.0.1" in APP_URL else "https"
)

CSRF_TRUSTED_ORIGINS = [APP_URL]

CHATWOOT_TOKEN = os.environ.get('CHATWOOT_TOKEN')
CHATWOOT_IDENTITY_VALIDATION = os.environ.get('CHATWOOT_IDENTITY_VALIDATION')

SECRET = os.environ.get('SECRET')

MOCK_PHONE_VERIFICATION = False
