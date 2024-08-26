import hmac
import hashlib
import json
import urllib
import logging

from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from tickets.models import Order, NewTicket, OrderTicket
import mercadopago


@csrf_exempt
def mercadopago_webhook(request):
    if request.method == 'POST':
        try:
            if not verify_hmac_request(request):
                logging.info("HMAC verification failed")
                return JsonResponse({"status": "forbidden"}, status=403)

            payload = json.loads(request.body)
            logging.info("Webhook payload:")
            logging.info(payload)

            if payload['action'] == 'payment.created':
                handle_payment_created(payload)

            return JsonResponse({"status": "success"}, status=200)

        except AttributeError as e:
            logging.error(f"Attribute error: {str(e)}")
            return JsonResponse({"status": "error", "message": "Internal attribute error"}, status=500)

        except KeyError as e:
            logging.error(f"Key error: {str(e)}")
            return JsonResponse({"status": "error", "message": f"Missing key: {str(e)}"}, status=400)

        except Exception as e:
            logging.error(f"Unexpected error: {str(e)}")
            return JsonResponse({"status": "error", "message": "An unexpected error occurred"}, status=500)
    else:
        return JsonResponse({"status": "method not allowed"}, status=405)


def verify_hmac_request(request):
    try:
        x_signature = request.headers.get("x-signature")
        x_request_id = request.headers.get("x-request-id")
        query_params = urllib.parse.parse_qs(request.GET.urlencode())
        data_id = query_params.get("data.id", [""])[0]

        ts, hash_value = parse_signature(x_signature)

        if verify_hmac(data_id, x_request_id, ts, hash_value):
            logging.info("HMAC verification passed")
            return True
        return False

    except Exception as e:
        logging.error(f"Error during HMAC verification: {str(e)}")
        raise e


def parse_signature(x_signature):
    ts = None
    hash_value = None
    parts = x_signature.split(",")

    for part in parts:
        key_value = part.split("=", 1)
        if len(key_value) == 2:
            key = key_value[0].strip()
            value = key_value[1].strip()
            if key == "ts":
                ts = value
            elif key == "v1":
                hash_value = value

    return ts, hash_value


def verify_hmac(data_id, x_request_id, ts, hash_value):
    secret = settings.MERCADOPAGO['WEBHOOK_SECRET']
    manifest = f"id:{data_id};request-id:{x_request_id};ts:{ts};"
    hmac_obj = hmac.new(secret.encode(), msg=manifest.encode(), digestmod=hashlib.sha256)
    sha = hmac_obj.hexdigest()
    return sha == hash_value


def handle_payment_created(payload):
    try:
        sdk = mercadopago.SDK(settings.MERCADOPAGO['ACCESS_TOKEN'])
        payment = sdk.payment().get(payload['data']['id'])['response']
        logging.info(payment)

        if payment['status'] == 'approved':
            process_order(payment)

    except KeyError as e:
        logging.error(f"Missing key in payload: {str(e)}")
        raise e

    except Exception as e:
        logging.error(f"Error handling payment creation: {str(e)}")
        raise e


def process_order(payment):
    try:
        order = Order.objects.get(key=payment['external_reference'])
        if order.status != Order.OrderStatus.PENDING:
            logging.info(f"Order {order.key} already confirmed")
            return

        order.status = Order.OrderStatus.PROCESSING
        order.save()

        if not tickets_available(order):
            refund_payment(order, payment)
            return

        mint_tickets(order)

        Order.objects.get(key=order.key).send_confirmation_email()

    except Order.DoesNotExist as e:
        logging.error(f"Order not found: {str(e)}")
        raise e

    except AttributeError as e:
        logging.error(f"Attribute error in processing order: {str(e)}")
        raise e

    except Exception as e:
        logging.error(f"Error processing order: {str(e)}")
        raise e


def tickets_available(order):
    try:
        tickets_remaining = order.event.tickets_remaining()
        if tickets_remaining < order.total_order_tickets():
            logging.info(f"Order {order.key} has more tickets than available")
            return False


        for order_ticket in OrderTicket.objects.filter(order=order).select_related('ticket_type').all():
            if order_ticket.quantity > order_ticket.ticket_type.ticket_count:
                logging.info(
                    f"Order {order.key} has more tickets of type {order_ticket.ticket_type.name} than available")
                return False

        return True

    except AttributeError as e:
        logging.error(f"Attribute error in checking ticket availability: {str(e)}")
        raise e

    except Exception as e:
        logging.error(f"Error checking ticket availability: {str(e)}")
        raise e


def refund_payment(order, payment):
    try:
        sdk = mercadopago.SDK(settings.MERCADOPAGO['ACCESS_TOKEN'])
        sdk.payment().refund(payment['id'], {"reason": "Tickets not available"})
        order.status = Order.OrderStatus.REFUNDED
        order.save()

    except Exception as e:
        logging.error(f"Error processing refund: {str(e)}")
        raise e


def mint_tickets(order):
    try:
        user_already_has_ticket = NewTicket.objects.filter(owner=order.user).exists()
        logging.info(f"user_already_has_ticket {user_already_has_ticket}")
        order_has_more_than_one_ticket_type = order.total_ticket_types() > 1
        logging.info(f"order_has_more_than_one_ticket_type {order_has_more_than_one_ticket_type}")

        order_tickets = OrderTicket.objects.filter(order=order)

        new_minted_tickets = []
        with transaction.atomic():
            for ticket in order_tickets:
                for _ in range(ticket.quantity):
                    new_ticket = NewTicket(
                        holder=order.user,
                        ticket_type=ticket.ticket_type,
                        order=order,
                        event=order.event,
                    )

                    if not user_already_has_ticket and not order_has_more_than_one_ticket_type:
                        new_ticket.owner = order.user
                        user_already_has_ticket = True

                    new_ticket.save()
                    new_minted_tickets.append(new_ticket)

            order.status = Order.OrderStatus.CONFIRMED
            order.save()

            for ticket in new_minted_tickets:
                logging.info(f"Minted {ticket}")

    except AttributeError as e:
        logging.error(f"Attribute error in minting tickets: {str(e)}")
        raise e

    except Exception as e:
        logging.error(f"Error minting tickets: {str(e)}")
        raise e
