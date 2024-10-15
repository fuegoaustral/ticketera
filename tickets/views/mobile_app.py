from django.contrib.auth import authenticate, get_user_model
from django.db.models import F
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from deprepagos import settings
from events.models import Event
from tickets.models import NewTicket


@api_view(['POST'])
@permission_classes([AllowAny])  # Allow any user to access this view without authentication
@authentication_classes([])  # No authentication required for this view
def post_login_jwt(request):
    username = request.data.get('username')
    password = request.data.get('password')

    user = authenticate(request, username=username, password=password)

    if user is not None:
        refresh = RefreshToken.for_user(user)
        return Response({
            'refresh': str(refresh),
            'access': str(refresh.access_token),
        }, status=status.HTTP_200_OK)
    else:
        return Response({'detail': 'Invalid credentials'}, status=status.HTTP_401_UNAUTHORIZED)


@api_view(['POST'])
@permission_classes([AllowAny])
def post_refresh_token_jwt(request):
    try:
        refresh = request.data.get('refresh')

        if not refresh:
            return Response({'detail': 'No refresh token provided'}, status=status.HTTP_400_BAD_REQUEST)

        # Decode the refresh token
        token = RefreshToken(refresh)

        # Get user from the token
        User = get_user_model()
        user_id = token['user_id']  # Assumes 'user_id' is the claim used in the token
        user = User.objects.get(id=user_id)

        # Access SIMPLE_JWT settings as dictionary keys
        rotate_refresh_tokens = settings.SIMPLE_JWT.get('ROTATE_REFRESH_TOKENS', False)

        # If ROTATE_REFRESH_TOKENS is True, create a new refresh token
        if rotate_refresh_tokens:
            token.blacklist()  # Blacklist the old refresh token if using token blacklisting
            new_refresh = RefreshToken.for_user(user)
            new_access_token = str(new_refresh.access_token)
            new_refresh_token = str(new_refresh)
        else:
            # Just get a new access token if rotation is not enabled
            new_access_token = str(token.access_token)
            new_refresh_token = refresh

        return Response({
            'access': new_access_token,
            'refresh': new_refresh_token,
        }, status=status.HTTP_200_OK)

    except User.DoesNotExist:
        return Response({'detail': 'User not found'}, status=status.HTTP_404_NOT_FOUND)
    except (InvalidToken, TokenError) as e:
        print(f"Token error: {str(e)}")
        return Response({'detail': 'Invalid token'}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        return Response({'detail': 'Something went wrong'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])  # Specify allowed methods here
@permission_classes([IsAuthenticated])
def get_secure_heartbeat(request):
    # This is a secured endpoint
    data = {"message": "This is secured data."}
    return Response(data, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_active_events(request):
    events = Event.objects.filter(active=True)

    data = [
        {
            "id": event.id,
            "name": event.name,
        } for event in events
    ]
    return Response(data, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_event_tickets(request, event_id):
    event = Event.objects.get(id=event_id)

    tickets = NewTicket.objects.filter(event=event, owner=F('holder'))

    data = [
        {
            "key": ticket.key,
            "first_name": ticket.holder.first_name,
            "last_name": ticket.holder.last_name,
            "email": ticket.holder.email,
            "phone": ticket.holder.profile.phone,
            "document_type": ticket.holder.profile.document_type,
            "document_number": ticket.holder.profile.document_number,
            "ticket_type": ticket.ticket_type.name,

        } for ticket in tickets
    ]
    return Response(data, status=status.HTTP_200_OK)


@api_view(['PUT'])
@permission_classes([IsAuthenticated])
def put_event_ticket(request, ticket_key):
    ticket = NewTicket.objects.get(key=ticket_key)

    if ticket is None:
        return Response({'detail': 'Ticket not found'}, status=status.HTTP_404_NOT_FOUND)

    data = {
        "key": ticket.key,
        "first_name": ticket.holder.first_name,
        "last_name": ticket.holder.last_name,
        "email": ticket.holder.email,
        "phone": ticket.holder.profile.phone,
        "document_type": ticket.holder.profile.document_type,
        "document_number": ticket.holder.profile.document_number,
        "ticket_type": ticket.ticket_type.name,
    }

    return Response(data, status=status.HTTP_200_OK)
