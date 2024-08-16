from pathlib import Path
import os

import django
from django.utils.encoding import smart_str

django.utils.encoding.smart_text = smart_str

import django.utils.translation as original_translation
from django.utils.translation import gettext_lazy

original_translation.ugettext_lazy = gettext_lazy

from django.utils.encoding import force_str

django.utils.encoding.force_text = force_str

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/3.2/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = 'django-insecure-s$(*=)6^h$p=d6e4tpv#-s7_hg&cl!vc@yzas371ubj=+ks&cc'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.environ.get('DEBUG', 'True') == 'True'

ALLOWED_HOSTS = [
    '127.0.0.1',
    'localhost',
    'bonos.fa2022.org',
    'eventos.fuegoaustral.org',
    os.environ.get('EXTRA_HOST')
]

APP_URL = os.environ.get('APP_URL', 'http://localhost:8000')

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
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.admin',
    'pipeline',
    'bootstrap5',
    'django_inlinecss',
    'django_s3_storage',
    'auditlog',
    'allauth',
    'allauth.account',
    # Optional -- requires install using `django-allauth[socialaccount]`.
    'allauth.socialaccount',
    'allauth.socialaccount.providers.google',

    'tickets.apps.TicketsConfig',
    'events.apps.EventsConfig',
]

MIDDLEWARE = [
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

]

ROOT_URLCONF = 'deprepagos.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [
            os.path.join(BASE_DIR, 'tickets/templates'),

        ],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'utils.context_processors.current_event',
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
        'PORT': '5432',
    }
}

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
# https://docs.djangoproject.com/en/3.2/howto/static-files/

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(PROJECT_ROOT, 'static')

MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(PROJECT_ROOT, 'media')

STATICFILES_STORAGE = 'pipeline.storage.PipelineManifestStorage'
STATICFILES_FINDERS = (
    'django.contrib.staticfiles.finders.FileSystemFinder',
    'django.contrib.staticfiles.finders.AppDirectoriesFinder',
    'pipeline.finders.PipelineFinder',
)

PIPELINE = {
    'PIPELINE_ENABLED': True,
    'SHOW_ERRORS_INLINE': True,
    'COMPILERS': (
        'libsasscompiler.LibSassCompiler',
    ),
    'CSS_COMPRESSOR': 'pipeline.compressors.csshtmljsminify.CssHtmlJsMinifyCompressor',
    'STYLESHEETS': {
        'main': {
            'source_filenames': (
                'scss/fuego.scss',
                'scss/global.scss',
            ),
            'output_filename': 'css/main.css',
            'extra_context': {
                'media': 'screen,projection',
            },
        },
    },
}

MERCADOPAGO = {
    # 'PUBLIC_KEY': 'TEST-320090f7-f283-4123-9d0a-ddcc5dca7652', # mauros@gmail.com
    # 'ACCESS_TOKEN': 'TEST-3993191188804171-120900-49b84931b82af80f4e67442917d5a311-2309703',
    # 'PUBLIC_KEY': 'TEST-adcf53df-bf76-4579-8f85-e3e1ef658c1c', #
    # 'ACCESS_TOKEN': 'TEST-8395362091404017-102216-655293c3d37f873676196ce190a66889-663579293',
    'PUBLIC_KEY': 'TEST-467cbbca-1aac-4d7f-be0b-53bcf92a3064',  # test_user_82107219@testuser.com / qatest8011
    'ACCESS_TOKEN': 'TEST-6630578586763408-121117-afe84675e0d0a70b7c67a1ade5909b2c-1037325933',
}

EMAIL_HOST = 'smtp.mailtrap.io'
EMAIL_HOST_USER = '1e47278bc26919'
EMAIL_HOST_PASSWORD = '1e355fb5adb1fd'
EMAIL_PORT = '2525'
EMAIL_USE_TLS = True

DEFAULT_FROM_EMAIL = 'Fuego Austral <bonos@eventos.fuegoaustral.org>'

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

# user comprador {"id":1037327132,"nickname":"TETE9670391","password":"qatest8330","site_status":"active","email":"test_user_43578812@testuser.com"}%
# user comprador {"id":1037346624,"nickname":"TETE9234065","password":"qatest9033","site_status":"active","email":"test_user_72163657@testuser.com"}%

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
        '': {
            'handlers': ['console'],
            'level': os.getenv('DJANGO_LOG_LEVEL', 'DEBUG'),
            'propagate': False,
        },
    },
}

# Email settings
ACCOUNT_EMAIL_REQUIRED = True
ACCOUNT_EMAIL_VERIFICATION = 'mandatory'
ACCOUNT_USERNAME_REQUIRED = False
ACCOUNT_USER_MODEL_USERNAME_FIELD = None

# Login settings
LOGIN_REDIRECT_URL = '/'
ACCOUNT_LOGOUT_REDIRECT_URL = '/'
ACCOUNT_AUTHENTICATION_METHOD = 'email'  # 'username_email', 'username'
ACCOUNT_EMAIL_CONFIRMATION_EXPIRE_DAYS = 3

SOCIALACCOUNT_EMAIL_AUTHENTICATION = True
SOCIALACCOUNT_LOGIN_ON_GET = True
ACCOUNT_LOGOUT_ON_GET = True
ACCOUNT_CONFIRM_EMAIL_ON_GET = True
ACCOUNT_EMAIL_CONFIRMATION_ANONYMOUS_REDIRECT_URL = '/accounts/verification-congrats/'
