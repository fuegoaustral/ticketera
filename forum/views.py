from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.core.paginator import Paginator

from .models import ForumSection, ForumThread, ForumMessage
from .forms import ForumUsernameForm, NewThreadForm, NewMessageForm, EditMessageForm


@login_required
def forum_index(request):
    """Página principal del foro - lista de secciones"""
    # Verificar si el usuario tiene forum_username
    if not hasattr(request.user, 'profile') or not request.user.profile.forum_username:
        if request.method == 'POST':
            form = ForumUsernameForm(request.POST)
            if form.is_valid():
                if not hasattr(request.user, 'profile'):
                    from user_profile.models import Profile
                    Profile.objects.create(user=request.user)
                
                request.user.profile.forum_username = form.cleaned_data['forum_username']
                request.user.profile.save()
                messages.success(request, f"¡Bienvenido al foro, {request.user.profile.forum_username}!")
                return redirect('forum:forum_index')
        else:
            form = ForumUsernameForm()
        
        return render(request, 'forum/welcome.html', {'form': form})
    
    sections = ForumSection.objects.filter(is_active=True).order_by('order', 'name')
    
    # Calcular estadísticas globales del foro
    total_threads = ForumThread.objects.filter(is_active=True).count()
    total_messages = ForumMessage.objects.filter(is_active=True).count()
    
    context = {
        'sections': sections,
        'total_threads': total_threads,
        'total_messages': total_messages,
    }
    return render(request, 'forum/index.html', context)


@login_required
def section_detail(request, section_id):
    """Detalle de una sección - lista de hilos"""
    section = get_object_or_404(ForumSection, id=section_id, is_active=True)
    threads = section.threads.filter(is_active=True).order_by('-is_pinned', '-updated_at')
    
    # Calcular la última página para cada hilo
    for thread in threads:
        messages_count = thread.messages.filter(is_active=True).count()
        thread.last_page = (messages_count + 19) // 20  # 20 mensajes por página
    
    context = {
        'section': section,
        'threads': threads,
    }
    return render(request, 'forum/section_detail.html', context)


@login_required
def thread_detail(request, thread_id):
    """Detalle de un hilo - lista de mensajes"""
    thread = get_object_or_404(ForumThread, id=thread_id, is_active=True)
    messages_list = thread.messages.filter(is_active=True).order_by('created_at')
    
    # Paginación - 20 mensajes por página
    paginator = Paginator(messages_list, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    if request.method == 'POST':
        form = NewMessageForm(request.POST)
        if form.is_valid():
            if thread.is_locked:
                messages.error(request, "Este hilo está bloqueado.")
            else:
                message = form.save(commit=False)
                message.thread = thread
                message.author = request.user
                message.save()
                
                # Actualizar la fecha de modificación del hilo
                thread.updated_at = timezone.now()
                thread.save()
                
                messages.success(request, "Mensaje publicado correctamente.")
                # Redirigir a la última página después de publicar
                from django.http import HttpResponseRedirect
                return HttpResponseRedirect(f'/foro/hilo/{thread.id}/?page={paginator.num_pages}')
    else:
        form = NewMessageForm()
    
    context = {
        'thread': thread,
        'messages_list': page_obj,
        'page_obj': page_obj,
        'form': form,
    }
    return render(request, 'forum/thread_detail.html', context)


@login_required
def new_thread(request, section_id):
    """Crear un nuevo hilo"""
    section = get_object_or_404(ForumSection, id=section_id, is_active=True)
    
    if request.method == 'POST':
        form = NewThreadForm(request.POST)
        if form.is_valid():
            thread = form.save(commit=False)
            thread.section = section
            thread.author = request.user
            thread.save()
            
            # Crear el primer mensaje del hilo
            content = request.POST.get('content', '')
            if content:
                ForumMessage.objects.create(
                    thread=thread,
                    author=request.user,
                    content=content
                )
            
            messages.success(request, "Hilo creado correctamente.")
            return redirect('forum:thread_detail', thread_id=thread.id)
    else:
        form = NewThreadForm()
    
    context = {
        'section': section,
        'form': form,
    }
    return render(request, 'forum/new_thread.html', context)


@login_required
def edit_message(request, message_id):
    """Editar un mensaje"""
    message = get_object_or_404(ForumMessage, id=message_id, is_active=True)
    
    # Verificar que el usuario sea el autor del mensaje
    if message.author != request.user:
        messages.error(request, "No tienes permisos para editar este mensaje.")
        return redirect('forum:thread_detail', thread_id=message.thread.id)
    
    if request.method == 'POST':
        form = EditMessageForm(request.POST, instance=message)
        if form.is_valid():
            form.save()
            messages.success(request, "Mensaje editado correctamente.")
            return redirect('forum:thread_detail', thread_id=message.thread.id)
    else:
        form = EditMessageForm(instance=message)
    
    context = {
        'message': message,
        'form': form,
    }
    return render(request, 'forum/edit_message.html', context)


@login_required
@require_POST
def delete_message(request, message_id):
    """Eliminar un mensaje (soft delete)"""
    message = get_object_or_404(ForumMessage, id=message_id, is_active=True)
    
    # Verificar que el usuario sea el autor del mensaje
    if message.author != request.user:
        return JsonResponse({'error': 'No tienes permisos para eliminar este mensaje.'}, status=403)
    
    message.is_active = False
    message.save()
    
    return JsonResponse({'success': True})