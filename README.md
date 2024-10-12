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
whoami # copy the output
open deprepagos/local_settings.py # and paste the output as `DB_USER` value
createdb deprepagos_development
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

The project is meant to be deployed in AWS Lambda. `django-zappa` handles the
upload and configuration for us. You can see the config in
[`zappa_settings.json`](zappa_settings.json).

To deploy from a local dev environment follow these steps:

1. Setup your personal AWS credentials as the `[default]` profile (needed by
   Zappa)

2. Deploy with `zappa update dev` (or `zappa update prod`)

  > [!IMPORTANT]  
  > In OS X you need to use a Docker image to have the same linux environment
  > as the one that runs in AWS Lambda to install the correct dependencies.
  >
  > ```
  > $ docker build . -t ticketera-zappashell
  > $ alias zappashell='docker run -ti -e AWS_PROFILE=default -v "$(pwd):/var/task" -v ~/.aws/:/root/.aws --rm ticketera-zappashell'
  > $ zappashell
zappashell> zappa update dev
  > ```

3. Update the static files to S3:

        $ python manage.py collectstatic --settings=deprepagos.settings_prod

## Adding a new Event

We have an [external Google
doc](https://docs.google.com/document/d/1_8NBQMMYZ68ABRQs2Fy-BX296OZnTdzzGWp6yNr_KEU/edit)
with instructions on how to create a new `Event` for the community members in
charge of communication and design.

When starting to prepare a new Event share this doc with the appropiate people.
