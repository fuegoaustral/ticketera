import base64
import json
import os
import zipfile

import jwt
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

from cryptography.hazmat.backends import default_backend
from asn1crypto.cms import ContentInfo, SignedData, SignerInfo, CMSAttributes, CMSAttribute, CMSAlgorithmProtection
from asn1crypto.core import OctetString, SetOf

from cryptography.hazmat.primitives import serialization
from django.db.models import Sum
from django.http import HttpResponseRedirect, JsonResponse, HttpResponse

from events.models import Event
from tickets.models import TicketType, Order
import logging


def is_order_valid(order):
    num_tickets = order.ticket_set.count()
    ticket_types = TicketType.objects.get_available(order.coupon, order.ticket_type.event)

    try:
        ticket_type = ticket_types.get(pk=order.ticket_type.pk)
    except TicketType.DoesNotExist:
        return False

    if ticket_type.available_tickets < num_tickets:
        return False

    if order.coupon and order.coupon.tickets_remaining() < num_tickets:
        return False

    if ticket_type.event.tickets_remaining() < num_tickets:
        return False
    return True


def _complete_order(order):
    logging.info('completing order')
    order.status = Order.OrderStatus.CONFIRMED
    logging.info('saving order')
    order.save()
    logging.info('redirecting')
    return HttpResponseRedirect(order.get_resource_url())


def available_tickets_for_user(user):
    from tickets.models import Order

    try:
        event = Event.objects.get(active=True)
    except Event.DoesNotExist:
        return 0

    # Sum the quantity of tickets in confirmed orders for this event and user
    tickets_bought = (Order.objects
                      .filter(email=user.email)
                      .filter(order_tickets__ticket_type__event=event)
                      .filter(status=Order.OrderStatus.CONFIRMED)
                      .annotate(total_quantity=Sum('order_tickets__quantity'))
                      .aggregate(tickets_bought=Sum('total_quantity'))
                      )['tickets_bought'] or 0

    return event.max_tickets_per_order - tickets_bought




def load_private_key():
    encrypted_key_base64 = os.environ['PRIVATE_KEY'].encode('utf-8')

    # Convert from base64
    encrypted_key_bytes = base64.b64decode(encrypted_key_base64)

    # Load and decrypt the key using the passphrase
    return serialization.load_pem_private_key(
        encrypted_key_bytes,
        password=os.environ['PRIVATE_KEY_KEY'].encode('utf-8'),
        backend=default_backend()
    )


def sign_pass(pass_data, private_key):
    # Convert the pass_data dictionary to JSON and encode it as bytes
    pass_json = json.dumps(pass_data, separators=(',', ':')).encode('utf-8')

    # Compute the SHA-256 hash of the pass.json data
    digest = hashes.Hash(hashes.SHA256(), backend=default_backend())
    digest.update(pass_json)
    digest_value = digest.finalize()

    # Sign the digest using the private key
    signature = private_key.sign(
        digest_value,
        padding.PKCS1v15(),
        hashes.SHA256()
    )

    # Create the ASN.1 structure for the signature
    signed_data = SignedData({
        'version': 'v1',
        'digest_algorithms': [CMSAlgorithmProtection({
            'digest_algorithm': {'algorithm': 'sha256'},
        })],
        'encap_content_info': {
            'content_type': 'data',
            'content': OctetString(pass_json),
        },
        'signer_infos': [
            SignerInfo({
                'version': 'v1',
                'sid': {
                    'issuer_and_serial_number': {
                        'issuer': None,  # Add your certificate issuer here
                        'serial_number': None,  # Add your certificate serial number here
                    }
                },
                'digest_algorithm': {
                    'algorithm': 'sha256',
                },
                'signature_algorithm': {
                    'algorithm': 'rsassa_pkcs1v15',
                },
                'signature': OctetString(signature),
                'signed_attrs': SetOf({
                    CMSAttribute({
                        'type': 'message_digest',
                        'values': [digest_value]
                    })
                }),
            })
        ]
    })

    # Wrap the signed data in a ContentInfo structure
    content_info = ContentInfo({
        'content_type': 'signed_data',
        'content': signed_data,
    })

    # Return the DER-encoded signature
    return content_info.dump()
