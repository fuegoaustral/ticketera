from django.test import TestCase

# Create your tests here.


class ScriptPrefixResetTests(TestCase):
    def test_links_built_after_a_stage_request_have_no_stage_prefix(self):
        from django.core.handlers.wsgi import WSGIHandler
        from django.test import RequestFactory
        from django.urls import reverse

        # Como Zappa: un request por la URL de API Gateway, con el stage como SCRIPT_NAME.
        environ = RequestFactory().get('/', SCRIPT_NAME='/prod', SERVER_NAME='testserver').environ
        response = WSGIHandler()(environ, lambda status, headers: None)
        response.close()
        self.assertEqual(reverse('art_dashboard'), '/mi-fuego/arte/')
