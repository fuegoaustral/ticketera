import logging
import os
import requests
from django.conf import settings
from events.models import Event
from tickets.models import Order

logger = logging.getLogger(__name__)


def check_pending_payments(event, context):
    """
    Scheduled task to check pending orders and verify payment status with MercadoPago.
    Runs every 5 minutes via Zappa scheduled events.
    
    Args:
        event: AWS Lambda event (unused but required by Zappa)
        context: AWS Lambda context (unused but required by Zappa)
    """
    logger.info("=" * 80)
    logger.info("Payment Check Cron Job Started")
    logger.info("=" * 80)
    
    try:
        # Get all active events
        active_events = Event.get_active_events()
        
        if not active_events.exists():
            logger.warning("No active events found. Skipping payment check.")
            return
        
        logger.info(f"Found {active_events.count()} active event(s)")
        
        # Get pending orders for all active events
        pending_orders = Order.objects.filter(
            status=Order.OrderStatus.PENDING,
            event__in=active_events
        ).values('id', 'key', 'event_id')
        
        logger.info(f"Found {pending_orders.count()} pending orders to check across all active events")
        
        if pending_orders.count() == 0:
            logger.info("No pending orders found. Exiting.")
            return
        
        # Get MercadoPago access token from environment
        access_token = os.environ.get('MERCADOPAGO_ACCESS_TOKEN') or settings.MERCADOPAGO.get('ACCESS_TOKEN')
        
        if not access_token:
            logger.error("MERCADOPAGO_ACCESS_TOKEN not found in environment variables")
            return
        
        approved_count = 0
        error_count = 0
        
        # Check each pending order
        for order_data in pending_orders:
            order_id = order_data['id']
            order_key = str(order_data['key'])
            event_id = order_data['event_id']
            
            # Get event name for logging
            try:
                event = Event.objects.get(id=event_id)
                event_name = event.name
            except Event.DoesNotExist:
                event_name = f"Event ID {event_id}"
            
            logger.info(f"Checking order ID: {order_id}, Key: {order_key}, Event: {event_name} (ID: {event_id})")
            
            try:
                # Call MercadoPago API to check payment status
                url = f"https://api.mercadopago.com/merchant_orders/search?external_reference={order_key}"
                headers = {
                    'Content-Type': 'application/json',
                    'Authorization': f'Bearer {access_token}'
                }
                
                logger.info(f"Making request to MercadoPago API: {url}")
                response = requests.get(url, headers=headers, timeout=30)
                
                logger.info(f"MercadoPago API response status: {response.status_code}")
                logger.info(f"MercadoPago API response body: {response.text}")
                
                if response.status_code != 200:
                    logger.error(f"Error calling MercadoPago API for order {order_key}: Status {response.status_code}")
                    error_count += 1
                    continue
                
                data = response.json()
                logger.info(f"Full MercadoPago response: {data}")
                
                # Check if payment is approved
                elements = data.get('elements', [])
                
                if not elements:
                    logger.info(f"No elements found in MercadoPago response for order {order_key}")
                    continue
                
                # Get the first merchant order
                merchant_order = elements[0]
                payments = merchant_order.get('payments', [])
                
                if not payments:
                    logger.info(f"No payments found in merchant order for order {order_key}")
                    continue
                
                # Check if any payment is approved
                payment_approved = False
                approved_payment = None
                for payment in payments:
                    payment_status = payment.get('status')
                    logger.info(f"Payment status for order {order_key}: {payment_status}")
                    
                    if payment_status == 'approved':
                        payment_approved = True
                        approved_payment = payment
                        logger.info(f"Payment APPROVED for order {order_key}")
                        break
                
                if payment_approved:
                    # Update order status to PROCESSING (this will trigger ticket emission)
                    try:
                        order = Order.objects.get(id=order_id, key=order_key)
                        
                        if order.status != Order.OrderStatus.PENDING:
                            logger.info(f"Order {order_key} is no longer PENDING (current status: {order.status}). Skipping update.")
                            continue
                        
                        logger.info(f"Updating order {order_key} status from PENDING to PROCESSING")
                        order.status = Order.OrderStatus.PROCESSING
                        # Store the merchant order data (includes payment info)
                        order.processor_callback = data
                        # Try to get net_received_amount from the approved payment
                        if approved_payment:
                            transaction_details = approved_payment.get('transaction_details', {})
                            net_received = transaction_details.get('net_received_amount')
                            if net_received:
                                order.net_received_amount = net_received
                                logger.info(f"Set net_received_amount to {net_received} for order {order_key}")
                        order.save()
                        
                        logger.info(f"Successfully updated order {order_key} to PROCESSING status")
                        approved_count += 1
                        
                    except Order.DoesNotExist:
                        logger.error(f"Order {order_key} not found in database")
                        error_count += 1
                    except Exception as e:
                        logger.error(f"Error updating order {order_key}: {str(e)}", exc_info=True)
                        error_count += 1
                else:
                    logger.info(f"Payment not approved yet for order {order_key}")
                    
            except requests.exceptions.RequestException as e:
                logger.error(f"Network error checking order {order_key}: {str(e)}", exc_info=True)
                error_count += 1
            except Exception as e:
                logger.error(f"Unexpected error checking order {order_key}: {str(e)}", exc_info=True)
                error_count += 1
        
        logger.info("=" * 80)
        logger.info(f"Payment Check Cron Job Completed")
        logger.info(f"Orders approved and updated: {approved_count}")
        logger.info(f"Errors encountered: {error_count}")
        logger.info(f"Total orders checked: {pending_orders.count()}")
        logger.info("=" * 80)
        
    except Exception as e:
        logger.error(f"Fatal error in payment check cron job: {str(e)}", exc_info=True)
        raise

