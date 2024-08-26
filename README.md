# Ticketera de FA

## Development

### Installation

```sh
# install PostgreSQL (v14.11 works perfectly) and Python<3.10 (v3.9.19 works perfectly)
python3 -m venv venv # or `python -m venv venv` if you have python 3 as default
source venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
cp deprepagos/local_settings.py.example deprepagos/local_settings.py
createdb deprepagos_development
```

## Set envs.

You can copy the values from dev environment.

### DB

Use your recently created local DB

```
DB_DATABASE
DB_HOST
DB_PORT
DB_USER
```

### MercadoPago

Create a SELLER TEST USER in MercadoPago and set the following
envs. [Instructions here]('https://www.mercadopago.com.ar/developers/es/docs/your-integrations/test/accounts')

Set the envs `MERCADOPAGO_PUBLIC_KEY`, `MERCADOPAGO_ACCESS_TOKEN` with the onces from the SELLER TEST USER.

Then set up a [webhook]('https://www.mercadopago.com.ar/developers/es/docs/your-integrations/notifications/webhooks')
pointing to `{your local env url}/webhooks/mercadopago`. And set the env `MERCADOPAGO_WEBHOOK_SECRET` with the secret
you set in the webhook creation.

I recommend using
a [cloudflare tunnel]('https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/get-started/create-remote-tunnel/'),
or  [ngrok]('https://ngrok.com/), or similar to expose your local server to the internet.

### Login with Google

Create a project in Google Cloud Platform and enable the Google+ API. Then create OAuth 2.0 credentials and set the envs
`GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` with the values from the credentials.

On the OAuth consent screen, set the authorized redirect URIs to `{your local env url}/accounts/google/login/callback/`







### Once you have the envs set up, you can run the following commands:

```sh
python manage.py migrate
whoami # copy the output
python manage.py createsuperuser # paste the output as username, leave email empty, and set some password
deactivate # only if you want to deactivate the virtualenv
```


For email testing use https://mailtrap.io/

### Running

```sh
source venv/bin/activate # if not already activated from before
python manage.py runserver
deactivate # if you want to deactivate the virtualenv
```

## Deploy

### DEV

Just push to the `dev` branch and the pipeline will deploy to the dev environment.


### PROD

Please don't push to the `main` branch directly. Create a PR and merge it on dev first. Then create a PR from dev to main.

`TODO ongoing: The pipeline will deploy to the prod environment.`

IF for some horrible reason you need to push to main directly, PLEASE, make sure to backport the changes to dev afterwards.



