from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required

from tickets.forms import ProfileStep1Form, ProfileStep2Form


@login_required
def complete_profile(request):
    profile = request.user.profile
    error_message = None
    code_sent = False

    if profile.profile_completion == 'NONE':
        if request.method == 'POST':
            form = ProfileStep1Form(request.POST, instance=profile, user=request.user)
            if form.is_valid():
                form.save()
                profile.profile_completion = 'INITIAL_STEP'
                profile.save()
                return redirect('complete_profile')
        else:
            form = ProfileStep1Form(instance=profile, user=request.user)
        return render(request, 'account/complete_profile_step1.html', {'form': form})

    elif profile.profile_completion == 'INITIAL_STEP':
        form = ProfileStep2Form(request.POST or None, instance=profile)
        if request.method == 'POST':
            if 'send_code' in request.POST:
                if form.is_valid():
                    form.save()
                    form.send_verification_code()
                    code_sent = True
            elif 'verify_code' in request.POST:
                code_sent = True
                form = ProfileStep2Form(request.POST, instance=profile, code_sent=True)
                if form.is_valid():
                    if form.verify_code():
                        profile.profile_completion = 'COMPLETE'
                        profile.save()
                        return profile_congrats(request)
                    else:
                        error_message = "Código inválido. Por favor, intenta de nuevo."
            else:
                form = ProfileStep2Form(request.POST, instance=profile, code_sent=True)

        return render(request, 'account/complete_profile_step2.html', {
            'form': form,
            'error_message': error_message,
            'code_sent': code_sent,
            'profile': profile
        })
    else:
        return redirect('home')


def profile_congrats(request):
    return render(request, 'account/profile_congrats.html')


@login_required
def verification_congrats(request):
    return render(request, 'account/verification_congrats.html')
