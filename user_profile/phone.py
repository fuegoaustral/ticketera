"""Phone numbers as WhatsApp wants them: international digits, Argentine mobiles with the 9."""
import re

# Argentine area codes are 2 (only 11), 3 or 4 digits; these are the 3-digit ones.
_AR_AREAS_3 = {
    '220', '221', '223', '230', '236', '237', '249', '260', '261', '263', '264', '266', '280', '291',
    '294', '297', '298', '299', '336', '341', '342', '343', '345', '348', '351', '353', '358', '362',
    '364', '370', '376', '379', '380', '381', '383', '385', '387', '388',
}


def _area_length(national):
    if national.startswith('11'):
        return 2
    return 3 if national[:3] in _AR_AREAS_3 else 4


def _argentine_national(digits):
    """Area code + number (10 digits), without 0, 15 or the international 9."""
    if digits.startswith('0'):
        digits = digits[1:]
    if len(digits) == 10 and digits.startswith('15'):
        # Escrito como celular local (15 xxxx-xxxx) sin característica: el AMBA.
        return '11' + digits[2:]
    if len(digits) == 12:
        area = _area_length(digits)
        if digits[area:area + 2] == '15':
            return digits[:area] + digits[area + 2:]
    return digits


def _split(phone):
    """(country code, national digits) or None. National digits only for Argentina."""
    raw = (phone or '').strip()
    digits = re.sub(r'\D', '', raw)
    if not digits:
        return None
    international = raw.startswith('+') or digits.startswith('00')
    digits = digits.lstrip('0') if digits.startswith('00') else digits
    if digits.startswith('54') and (international or len(digits) >= 12):
        national = digits[2:]
        if national.startswith('9'):
            national = national[1:]
        return '54', _argentine_national(national)
    if international:
        return None, digits
    return '54', _argentine_national(digits)


def whatsapp_number(phone):
    """Digits for wa.me / web.whatsapp.com links, or '' when there is no number."""
    parts = _split(phone)
    if not parts:
        return ''
    country, national = parts
    return f'549{national}' if country == '54' else national


def display_phone(phone):
    """A readable number: +54 9 11 5555-1234, +54 9 3525 41-9619, or +<digits> abroad."""
    parts = _split(phone)
    if not parts:
        return ''
    country, national = parts
    if country != '54':
        return f'+{national}'
    if len(national) != 10:
        return f'+54 9 {national}'
    area = _area_length(national)
    number = national[area:]
    return f'+54 9 {national[:area]} {number[:-4]}-{number[-4:]}'
