# 🎫 Ticketera de FA

> **Sistema de venta de tickets para eventos de Fuego Austral** 🔥

[![Python](https://img.shields.io/badge/Python-3.13-blue.svg)](https://python.org)
[![Django](https://img.shields.io/badge/Django-4.2-green.svg)](https://djangoproject.com)
[![AWS Lambda](https://img.shields.io/badge/AWS-Lambda-orange.svg)](https://aws.amazon.com/lambda/)
[![Zappa](https://img.shields.io/badge/Zappa-0.60.2-purple.svg)](https://github.com/Miserlou/Zappa)

## 🚀 Características

- 🎟️ **Gestión de eventos** - Crear y administrar eventos de manera sencilla
- 💳 **Pagos integrados** - Integración con MercadoPago para procesamiento de pagos
- 🔐 **Autenticación** - Login con Google OAuth2
- 📧 **Notificaciones** - Sistema de emails automáticos
- ☁️ **Deploy automático** - CI/CD con GitHub Actions
- 🐍 **Python 3.13** - Última versión de Python con mejoras de rendimiento

## 🛠️ Desarrollo Local

### 📋 Requisitos Previos

- **PostgreSQL** (v16.8 en producción, v14.11+ para desarrollo local) 🐘
- **Python 3.13** (última versión) 🐍
- **Git** para clonar el repositorio 📦

### ⚙️ Configuración del Entorno

#### 🔧 Variables de Entorno

Crea un archivo `.env` basado en el template:

```bash
cp .env.example .env
```

#### 🐍 Configuración de Python

1. **Crear entorno virtual** 🌐

```bash
python3.13 -m venv venv
source venv/bin/activate
```

> 💡 **Tip**: Para salir del entorno virtual ejecuta `deactivate`

2. **Instalar dependencias** 📦

```bash
(venv)$ pip install -r requirements.txt
(venv)$ pip install -r requirements-dev.txt
```

3. **Configurar settings locales** ⚙️

```bash
(venv)$ cp deprepagos/local_settings.py.example deprepagos/local_settings.py
```

#### 🗄️ Base de Datos Local

1. **Iniciar PostgreSQL** 🚀

```bash
# macOS
brew services start postgresql

# Ubuntu/Debian
sudo systemctl start postgresql
```

2. **Crear base de datos** 🏗️

```bash
(venv)$ createdb deprepagos_development
```

3. **Aplicar migraciones** 🔄

```bash
(venv)$ python manage.py migrate
```

4. **Crear usuario administrador** 👤

```bash
(venv)$ python manage.py createsuperuser
```

### 🔗 Integraciones Externas

#### 💳 MercadoPago

1. **Crear usuario de prueba** en [MercadoPago](https://www.mercadopago.com.ar/developers/es/docs/your-integrations/test/accounts) 🧪
2. **Configurar variables**:
   - `MERCADOPAGO_PUBLIC_KEY`
   - `MERCADOPAGO_ACCESS_TOKEN`
   - `MERCADOPAGO_WEBHOOK_SECRET`

3. **Configurar webhook** apuntando a `{tu_url_local}/webhooks/mercadopago` 🔗

> 🌐 **Para exponer tu servidor local**: Usa [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/get-started/create-remote-tunnel/) o [ngrok](https://ngrok.com/)

#### 🔐 Google OAuth2

1. **Crear proyecto** en [Google Cloud Platform](https://console.cloud.google.com/) ☁️
2. **Habilitar Google+ API** 📡
3. **Crear credenciales OAuth 2.0** 🔑
4. **Configurar variables**:
   - `GOOGLE_CLIENT_ID`
   - `GOOGLE_CLIENT_SECRET`
5. **Agregar URI de redirección**: `{tu_url_local}/accounts/google/login/callback/` 🔄

#### 📧 Testing de Emails

Usa [Mailtrap](https://mailtrap.io/) para testing de emails 📬

1. Crear cuenta en Mailtrap
2. Obtener credenciales SMTP de **Email Testing > Inboxes > SMTP**
3. Configurar en tu `.env`

### 🏃‍♂️ Ejecutar el Servidor

```bash
(venv)$ python manage.py runserver
```

¡Listo! 🎉 Tu aplicación estará disponible en `http://127.0.0.1:8000`

## 🚀 Deploy

> ⚠️ **IMPORTANTE**: Todos los deploys se realizan **exclusivamente por CI/CD** (GitHub Actions). No se hacen deploys manuales.

### 🔄 Flujo de Deploy Completo

#### 1️⃣ **Desarrollo → Dev Environment**

```bash
# 1. Crear feature branch desde dev
git checkout dev
git pull origin dev
git checkout -b feature/nueva-funcionalidad

# 2. Hacer cambios y commit
git add .
git commit -m "feat: agregar nueva funcionalidad"

# 3. Push y crear PR a dev
git push origin feature/nueva-funcionalidad
# Crear PR en GitHub: feature/nueva-funcionalidad → dev
```

> ⚡ **Deploy automático a dev**: Al mergear el PR a `dev`, GitHub Actions despliega automáticamente

#### 2️⃣ **Testing en Dev Environment**

- 🧪 **Probar** la funcionalidad en `https://dev.fuegoaustral.org`
- ✅ **Verificar** que todo funciona correctamente
- 🔍 **Revisar** logs y métricas

#### 3️⃣ **Dev → Production**

```bash
# 1. Crear PR de dev a main
# En GitHub: Crear PR dev → main

# 2. Revisar y mergear
# Después de revisión, mergear el PR

# 3. Deploy automático a producción
# GitHub Actions despliega automáticamente a prod
```

> 🚀 **Deploy automático a prod**: Al mergear `dev` → `main`, se despliega automáticamente a producción

### 📋 Reglas de Deploy

#### ✅ **Permitido**
- ✅ Push a `feature/*` branches
- ✅ PRs a `dev` branch
- ✅ PRs de `dev` a `main`

#### 🚫 **Prohibido**
- 🚫 Push directo a `dev` (excepto hotfixes críticos)
- 🚫 Push directo a `main` (NUNCA)
- 🚫 Deploys manuales con Zappa

### 🆘 **Hotfixes Críticos**

En caso de emergencia crítica:

```bash
# 1. Crear hotfix desde main
git checkout main
git pull origin main
git checkout -b hotfix/fix-critico

# 2. Aplicar fix y commit
git add .
git commit -m "hotfix: fix crítico urgente"

# 3. Push y crear PR directo a main
git push origin hotfix/fix-critico
# Crear PR: hotfix/fix-critico → main

# 4. OBLIGATORIO: Backport a dev después
git checkout dev
git cherry-pick <commit-hash>
git push origin dev
```

### 🏗️ **Configuración Docker (Solo para Emergencias)**

> ⚠️ **Solo usar en emergencias**: El deploy normal es 100% automático

```bash
# Construir imagen Docker
docker build . -t ticketera-zappashell

# Crear alias para facilitar el uso
alias zappashell='docker run -ti -e AWS_PROFILE=ticketera -v "$(pwd):/var/task" -v ~/.aws/:/root/.aws --rm ticketera-zappashell'

# Usar el shell (solo emergencias)
zappashell
zappashell> zappa update prod
```

### 📁 **Archivos Estáticos**

Los archivos estáticos se manejan automáticamente en el pipeline:

```bash
# Esto se ejecuta automáticamente en CI/CD
python manage.py collectstatic --settings=deprepagos.settings_prod
```

## 🎪 Agregar un Nuevo Evento

📖 **Documentación completa**: [Google Doc](https://docs.google.com/document/d/1_8NBQMMYZ68ABRQs2Fy-BX296OZnTdzzGWp6yNr_KEU/edit)

> 💡 **Tip**: Comparte este documento con el equipo de comunicación y diseño cuando prepares un nuevo evento

## 🏗️ Arquitectura

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Frontend      │    │   Django App     │    │   AWS Lambda    │
│   (Templates)   │◄──►│   (Python 3.13)  │◄──►│   (Zappa)       │
└─────────────────┘    └──────────────────┘    └─────────────────┘
                                │
                                ▼
                       ┌──────────────────┐
                       │   PostgreSQL     │
                       │   (RDS v16.8)    │
                       └──────────────────┘
```

## 🛠️ Tecnologías

- **Backend**: Django 4.2 + Python 3.13 🐍
- **Base de Datos**: PostgreSQL 16.8 (producción) / 14.11+ (desarrollo) 🐘
- **Deploy**: AWS Lambda + Zappa ☁️
- **CI/CD**: GitHub Actions 🚀
- **Pagos**: MercadoPago 💳
- **Auth**: Google OAuth2 🔐
- **Emails**: Django + SMTP 📧

## 📞 Soporte

¿Necesitas ayuda? 🤔

- 📧 **Email**: [contacto@fuegoaustral.org](mailto:contacto@fuegoaustral.org)
- 🐛 **Issues**: [GitHub Issues](https://github.com/fuegoaustral/ticketera)


---

<div align="center">

**Hecho con ❤️ por el equipo de Fuego Austral** 🔥

</div>