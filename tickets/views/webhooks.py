import hmac
import hashlib
import json
import urllib
import logging
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from tickets.models import Order
import mercadopago

@csrf_exempt
def mercadopago_webhook(request):
    if request.method == 'POST':
        try:
            xSignature = request.headers.get("x-signature")
            xRequestId = request.headers.get("x-request-id")
            queryParams = urllib.parse.parse_qs(request.GET.urlencode())
            dataID = queryParams.get("data.id", [""])[0]
            parts = xSignature.split(",")

            ts = None
            hash = None

            for part in parts:
                keyValue = part.split("=", 1)
                if len(keyValue) == 2:
                    key = keyValue[0].strip()
                    value = keyValue[1].strip()
                    if key == "ts":
                        ts = value
                    elif key == "v1":
                        hash = value

            secret = settings.MERCADOPAGO['WEBHOOK_SECRET']
            manifest = f"id:{dataID};request-id:{xRequestId};ts:{ts};"
            hmac_obj = hmac.new(secret.encode(), msg=manifest.encode(), digestmod=hashlib.sha256)
            sha = hmac_obj.hexdigest()

            if sha == hash:
                logging.info("HMAC verification passed")
                payload = json.loads(request.body)
                logging.info("Webhook payload:")
                logging.info(payload)

                if payload['action'] == 'payment.created':
                    sdk = mercadopago.SDK(settings.MERCADOPAGO['ACCESS_TOKEN'])
                    payment = sdk.payment().get(payload['data']['id'])['response']
                    logging.info(payment)
                    if payment['status'] == 'approved':
                        order = Order.objects.get(key=payment['external_reference'])
                        if order.status != Order.OrderStatus.PENDING:
                            logging.info(f"Order {order.key} already confirmed")
                            return JsonResponse({"status": "success"}, status=200)

                        order.status = Order.OrderStatus.PROCESSING
                        order.save()

                        # TODO mint tickets

                        order.status = Order.OrderStatus.CONFIRMED
                        order.save()
                        logging.info(f"Order {order.key} confirmed")

                return JsonResponse({"status": "success"}, status=200)
            else:
                logging.info("HMAC verification failed")
                return JsonResponse({"status": "forbidden"}, status=403)

        except Exception as e:
            logging.error(f"Error during HMAC verification: {str(e)}")
            return JsonResponse({"status": "error", "message": str(e)}, status=400)
    else:
        return JsonResponse({"status": "method not allowed"}, status=405)
