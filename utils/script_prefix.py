from django.urls import set_script_prefix


def reset_script_prefix(**kwargs):
    """Vuelve el prefijo de URLs a '/' al terminar cada request.

    Django guarda el prefijo del request (SCRIPT_NAME) a nivel de proceso. En Lambda el contenedor queda
    tibio: si un request entró por la URL de API Gateway (con el stage, '/prod/'), los crons y los
    `zappa manage` que corren después en el mismo contenedor arman links con '/prod/' adelante.
    """
    set_script_prefix('/')
