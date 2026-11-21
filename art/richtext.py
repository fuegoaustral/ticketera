"""Texto con formato mínimo para los mensajes a los equipos: negrita, cursiva, listas, links y botones.

El HTML del editor nunca se usa tal cual: `sanitize` lo reescribe con una lista cerrada de etiquetas y
sólo deja links http, https o mailto.
"""
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit

BUTTON_CLASS = 'msg-btn'
# Lo que escribe el editor (y lo que pegan desde otros lados) → la etiqueta que guardamos.
TAGS = {
    'p': 'p', 'div': 'p', 'br': 'br',
    'strong': 'strong', 'b': 'strong', 'em': 'em', 'i': 'em',
    'ul': 'ul', 'ol': 'ol', 'li': 'li', 'a': 'a',
}
DROPPED_WITH_CONTENT = {'script', 'style', 'head', 'title', 'iframe', 'object', 'embed', 'noscript', 'template', 'svg'}
SAFE_SCHEMES = {'http', 'https', 'mailto'}
BLOCKS = {'p', 'ul', 'ol', 'li'}


def safe_href(value):
    value = ''.join((value or '').split())
    try:
        scheme = urlsplit(value).scheme.lower()
    except ValueError:
        return ''
    return value if scheme in SAFE_SCHEMES else ''


class _Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.open = []  # las etiquetas que emitimos y siguen abiertas ('' = link descartado)
        self.dropping = 0

    def handle_starttag(self, tag, attrs):
        if tag in DROPPED_WITH_CONTENT:
            self.dropping += 1
            return
        if self.dropping:
            return
        name = TAGS.get(tag)
        if not name:
            return
        if name == 'br':
            self.out.append('<br>')
            return
        if name == 'a':
            attrs = dict(attrs)
            href = safe_href(attrs.get('href'))
            if not href or 'a' in self.open:
                self.open.append('')
                return
            button = BUTTON_CLASS in (attrs.get('class') or '').split()
            self.out.append(f'<a href="{escape(href, quote=True)}"' + (f' class="{BUTTON_CLASS}"' if button else '') + '>')
        else:
            # Un párrafo no contiene bloques y un ítem no contiene a su hermano: se cierran antes de abrir el nuevo.
            if name in ('p', 'ul', 'ol') and 'p' in self.open:
                self._close_to(('p',))
            if name == 'li':
                lists = [index for index, item in enumerate(self.open) if item in ('ul', 'ol')]
                if 'li' in self.open[(lists[-1] if lists else -1) + 1:]:
                    self._close_to(('li',))
            self.out.append(f'<{name}>')
        self.open.append(name)

    def _close_to(self, wanted):
        while self.open:
            last = self.open.pop()
            if last:
                self.out.append(f'</{last}>')
            if last in wanted:
                break

    def handle_startendtag(self, tag, attrs):
        if TAGS.get(tag) == 'br' and not self.dropping:
            self.out.append('<br>')

    def handle_endtag(self, tag):
        if tag in DROPPED_WITH_CONTENT:
            self.dropping = max(0, self.dropping - 1)
            return
        name = TAGS.get(tag)
        if self.dropping or not name or name == 'br':
            return
        wanted = ('a', '') if name == 'a' else (name,)
        if not any(item in wanted for item in self.open):
            return
        # Cierra hasta la etiqueta pedida: el resultado siempre queda bien anidado.
        self._close_to(wanted)

    def handle_data(self, data):
        if not self.dropping:
            self.out.append(escape(data, quote=False))

    def result(self):
        while self.open:
            last = self.open.pop()
            if last:
                self.out.append(f'</{last}>')
        return ''.join(self.out).strip()


def sanitize(html):
    """El HTML del editor reducido a p, br, strong, em, ul, ol, li y a[href] (con la marca de botón)."""
    parser = _Sanitizer()
    parser.feed(html or '')
    parser.close()
    return parser.result()


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.href = None
        self.link_text = []

    def handle_starttag(self, tag, attrs):
        if tag == 'br':
            self.out.append('\n')
        elif tag == 'li':
            self.out.append('\n• ')
        elif tag in ('p', 'ul', 'ol'):
            self.out.append('\n\n')
        elif tag == 'a':
            self.href, self.link_text = dict(attrs).get('href', ''), []

    def handle_endtag(self, tag):
        if tag in ('p', 'ul', 'ol'):
            self.out.append('\n\n')
        elif tag == 'a' and self.href is not None:
            label = ''.join(self.link_text).strip()
            target = self.href[7:] if self.href.startswith('mailto:') else self.href
            self.out.append(target if label in ('', target, self.href) else f'{label} ({target})')
            self.href = None

    def handle_data(self, data):
        (self.link_text if self.href is not None else self.out).append(data)


def to_text(clean_html):
    """El mensaje como texto legible, con la dirección de cada link: así queda en el historial de la propuesta."""
    parser = _Text()
    parser.feed(clean_html or '')
    parser.close()
    lines = [' '.join(line.split()) for line in ''.join(parser.out).split('\n')]
    text, blank = [], True
    for line in lines:
        if line or not blank:
            text.append(line)
        blank = not line
    return '\n'.join(text).strip()


EMAIL_BUTTON = (
    '<table role="presentation" border="0" cellpadding="0" cellspacing="0" class="btn btn-primary"><tbody><tr>'
    '<td align="left"><table role="presentation" border="0" cellpadding="0" cellspacing="0"><tbody><tr><td>'
    '<a href="{href}" target="_blank">{label}</a>'
    '</td></tr></tbody></table></td></tr></tbody></table>'
)


class _Email(HTMLParser):
    """Reescribe el HTML ya saneado para el email: los botones pasan a ser el botón de los emails del sitio."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.button = None
        self.reopen = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a' and BUTTON_CLASS in (attrs.get('class') or '').split():
            self.button = {'href': attrs.get('href', ''), 'label': []}
            # Una tabla no puede ir dentro de un párrafo: lo cerramos y lo reabrimos después del botón.
            if self.reopen and self.reopen[-1] == 'p':
                self.out.append('</p>')
        elif tag == 'a':
            self.out.append(f'<a href="{escape(attrs.get("href", ""), quote=True)}" target="_blank">')
        elif tag == 'br':
            self.out.append('<br>')
        else:
            self.out.append(f'<{tag}>')
            self.reopen.append(tag)

    def handle_endtag(self, tag):
        if tag == 'a' and self.button is not None:
            label = escape(''.join(self.button['label']).strip() or self.button['href'], quote=False)
            self.out.append(EMAIL_BUTTON.format(href=escape(self.button['href'], quote=True), label=label))
            self.button = None
            if self.reopen and self.reopen[-1] == 'p':
                self.out.append('<p>')
        elif tag == 'a':
            self.out.append('</a>')
        elif tag != 'br':
            if self.reopen and self.reopen[-1] == tag:
                self.reopen.pop()
            self.out.append(f'</{tag}>')

    def handle_data(self, data):
        if self.button is not None:
            self.button['label'].append(data)
        else:
            self.out.append(escape(data, quote=False))


def to_email_html(clean_html):
    parser = _Email()
    parser.feed(clean_html or '')
    parser.close()
    return ''.join(parser.out).replace('<p></p>', '')
