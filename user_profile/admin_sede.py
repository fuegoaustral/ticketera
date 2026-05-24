from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import redirect
from django.views.decorators.http import require_http_methods


@staff_member_required
@require_http_methods(['GET'])
def admin_sede_home_view(request):
    return redirect('admin_sede_members_view')
