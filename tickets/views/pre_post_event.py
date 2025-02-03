import os
from django.http import HttpResponse
from django.contrib.auth.decorators import login_required
from django.template import loader

@login_required()
def ingreso_anticipado(request):
    context = { 'form_url': f"https://docs.google.com/forms/u/0/d/e/1FAIpQLSfO2H29d6QY_K7QIp7A4s1-wX3IMCLQPH7cE7LLuLe8p3{os.environ.get('PRE_ID')}/formResponse" }
    template = loader.get_template('pre_post_event/ingreso_anticipado.html')
    return HttpResponse(template.render(context, request))

@login_required()
def ingreso_anticipado_proveedores(request):
    context = { 'form_url': f"https://docs.google.com/forms/u/0/d/e/1FAIpQLSddybkyHuel2SXtsRo75HScR25lEcN5bpbwMAkx_LKAbR{os.environ.get('PRE_PROV_ID')}/formResponse" }
    template = loader.get_template('pre_post_event/ingreso_anticipado_proveedores.html')
    return HttpResponse(template.render(context, request))

@login_required()
def late_checkout(request):
    context = { 'form_url': f"https://docs.google.com/forms/u/0/d/e/1FAIpQLScwYW8fpTss_ia2KZ5ggdyVm1DUiDO8mvZwzym8B0F87P{os.environ.get('LCO_ID')}/formResponse" }
    template = loader.get_template('pre_post_event/late_checkout.html')
    return HttpResponse(template.render(context, request))

@login_required()
def late_checkout_proveedores(request):
    context = { 'form_url': f"https://docs.google.com/forms/u/0/d/e/1FAIpQLSd_5_fCJOYXUuKbab41oUwMMW-IyKR1bdsky41CY40VYQ{os.environ.get('LCO_PROV_ID')}/formResponse" }
    template = loader.get_template('pre_post_event/late_checkout_proveedores.html')
    return HttpResponse(template.render(context, request))
