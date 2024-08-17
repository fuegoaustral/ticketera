from django.shortcuts import redirect
from django.urls import reverse

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
