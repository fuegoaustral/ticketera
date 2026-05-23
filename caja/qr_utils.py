import base64
import io

import qrcode


def qr_string_to_data_url(data):
    buffer = io.BytesIO()
    qrcode.make(data).save(buffer, format='PNG')
    encoded = base64.b64encode(buffer.getvalue()).decode('ascii')
    return f'data:image/png;base64,{encoded}'
