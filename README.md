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

### OS X

Setup aws credentials in `[default]` (needed in zappa_settings.json). Then run:

```
$ docker build . -t ticketera-zappashell
$ alias zappashell='docker run -ti -e AWS_PROFILE=default -v "$(pwd):/var/task" -v ~/.aws/:/root/.aws --rm ticketera-zappashell'
$ zappashell
> zappa update dev
```
