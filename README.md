# Ticketera de FA

## Development - local

### Requirements

- install PostgreSQL (v14.11 works perfectly)
- Python<3.10 (v3.9.19 works perfectly)

### Setup environment

#### Env variables

Set env variables in `.env` file. You can use `.env.example` as template to copy from.
```sh
cp .env.example .env
```

#### Python

1. Create python virtual environment and start it

```sh
python3 -m venv venv
source venv/bin/activate
```

Note: you can quit virtual environment by running deactivate 

```sh
(venv)$ deactivate
```

2. Install python dependencies

```sh
(venv)$ pip install -r requirements.txt
(venv)$ pip install -r requirements-dev.txt
```

3. Copy local settings
```sh
(venv)$ cp deprepagos/local_settings.py.example deprepagos/local_settings.py
```

#### Local DB

1. Start postgresql server
```sh
brew services start postgresql #for mac
```

2. Create db

```sh
(venv)$ createdb deprepagos_development
```

3. Apply db updates

```sh
(venv)$ python manage.py migrate
```

4. Create Django admin user

```sh
(venv)$ python manage.py createsuperuser # provide username, leave email empty, and set some password. You can use whoami in mac to get a username
```

#### Setup MercadoPago

Create a SELLER TEST USER in MercadoPago and set the following
envs. [Instructions here]('https://www.mercadopago.com.ar/developers/es/docs/your-integrations/test/accounts')

Set the envs `MERCADOPAGO_PUBLIC_KEY`, `MERCADOPAGO_ACCESS_TOKEN` with the onces from the SELLER TEST USER.

Then set up a [webhook]('https://www.mercadopago.com.ar/developers/es/docs/your-integrations/notifications/webhooks')
pointing to `{your local env url}/webhooks/mercadopago`. And set the env `MERCADOPAGO_WEBHOOK_SECRET` with the secret
you set in the webhook creation.

I recommend using
a [cloudflare tunnel]('https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/get-started/create-remote-tunnel/'),
or [ngrok]('https://ngrok.com/), or similar to expose your local server to the internet.

#### Setup Google authentication

Create a project in Google Cloud Platform and enable the Google+ API. Then create OAuth 2.0 credentials and set the envs
`GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` with the values from the credentials.

On the OAuth consent screen, set the authorized redirect URIs to `{your local env url}/accounts/google/login/callback/`

#### Setup email testing

For email testing use https://mailtrap.io/

1. Create an account
2. Get HOST, PORT, USERNAME and PASSWORD from Email Testing > Inboxes > SMTP

### Run environment

```sh
(venv)$ python manage.py runserver
```

## Deploy

### DEV

Just push to the `dev` branch and the pipeline will deploy to the dev environment.

### PROD
  > [!IMPORTANT]  
  > In OS X you need to use a Docker image to have the same linux environment
  > as the one that runs in AWS Lambda to install the correct dependencies.
  >
  > ```
  > $ docker build . -t ticketera-zappashell
  > $ alias zappashell='docker run -ti -e AWS_PROFILE=default -v "$(pwd):/var/task" -v ~/.aws/:/root/.aws --rm ticketera-zappashell'
  > $ zappashell
  > zappashell> zappa update dev
  > ```

Please don't push to the `main` branch directly. Create a PR and merge it on `dev` first. Then create a PR from `dev` to `main`.

`TODO ongoing: The pipeline will deploy to the prod environment.`

If for some horrible reason you need to push to `main` directly, PLEASE, make sure to backport the changes to `dev` afterwards.
3. Update the static files to S3:

        $ python manage.py collectstatic --settings=deprepagos.settings_prod

## Adding a new Event

We have an [external Google
doc](https://docs.google.com/document/d/1_8NBQMMYZ68ABRQs2Fy-BX296OZnTdzzGWp6yNr_KEU/edit)
with instructions on how to create a new `Event` for the community members in
charge of communication and design.

When starting to prepare a new Event share this doc with the appropiate people.
