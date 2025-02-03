import os
from django.http import HttpResponse
from django.contrib.auth.decorators import login_required
from django.template import loader

@login_required()
def ingreso_anticipado(request):
    context = { 'form_url': f"https://docs.google.com/forms/u/0/d/e/{os.environ.get('INGRESO_ANTICIPADO_FORM_ID')}/formResponse" }
    template = loader.get_template('pre_post_event/ingreso_anticipado.html')
    return HttpResponse(template.render(context, request))

@login_required()
def ingreso_anticipado_proveedores(request):
    context = { 'form_url': f"https://docs.google.com/forms/u/0/d/e/{os.environ.get('INGRESO_ANTICIPADO_PROVEEDORES_FORM_ID')}/formResponse" }
    template = loader.get_template('pre_post_event/ingreso_anticipado_proveedores.html')
    return HttpResponse(template.render(context, request))

@login_required()
def late_checkout(request):
    context = { 'form_url': f"https://docs.google.com/forms/u/0/d/e/{os.environ.get('LATE_CHECKOUT_FORM_ID')}/formResponse" }
    template = loader.get_template('pre_post_event/late_checkout.html')
    return HttpResponse(template.render(context, request))

@login_required()
def late_checkout_proveedores(request):
    context = { 'form_url': f"https://docs.google.com/forms/u/0/d/e/{os.environ.get('LATE_CHECKOUT_PROVEEDORES_FORM_ID')}/formResponse" }
    template = loader.get_template('pre_post_event/late_checkout_proveedores.html')
    return HttpResponse(template.render(context, request))
