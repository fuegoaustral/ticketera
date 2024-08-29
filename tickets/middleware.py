from django.shortcuts import redirect
from django.urls import reverse
from django.utils.deprecation import MiddlewareMixin


class ProfileCompletionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Check if user is authenticated and not staff
        if request.user.is_authenticated and not request.user.is_staff:
            # Allow access to logout URL and complete_profile URL
            if not request.path.startswith(reverse('account_logout')) and \
                    not request.path.startswith(reverse('complete_profile')):
                if request.user.profile.profile_completion != 'COMPLETE':
                    return redirect('complete_profile')
        return self.get_response(request)


class DeviceDetectionMiddleware(MiddlewareMixin):
    def process_request(self, request):
        user_agent = request.META.get('HTTP_USER_AGENT', '').lower()
        request.is_iphone = 'iphone' in user_agent
        request.is_android = 'android' in user_agent
