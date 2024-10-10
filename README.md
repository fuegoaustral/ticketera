# Ticketera de FA

## Deploy

The project is meant to be deployed in AWS Lambda. `django-zappa` handles the
upload and configuration for us. You can see the config in
[`zappa_settings.json`](zappa_settings.json).

To deploy from a local dev environment follow these steps:

1. Setup your personal AWS credentials as the `[default]` profile (needed by
   Zappa)

1. Deploy with `zappa update dev` (or `prod`)

    > [!IMPORTANT]  
    > In OS X you need to use a Docker image to have the same linux environment
    > as the one that runs in AWS Lambda to install the correct dependencies.
    > 
    > ```
    > $ docker build . -t ticketera-zappashell
    > $ alias zappashell='docker run -ti -e AWS_PROFILE=default -v "$(pwd):/var/task" -v ~/.aws/:/root/.aws --rm ticketera-zappashell'
    > $ zappashell
    > $ zappa update dev
    > ```

1. Update the static files to S3:

        $ python manage.py collectstatic --settings=deprepagos.settings_prod

## Adding a new Event

We have an [external Google
doc](https://docs.google.com/document/d/1_8NBQMMYZ68ABRQs2Fy-BX296OZnTdzzGWp6yNr_KEU/edit)
with instructions on how to create a new `Event` for the community members in
charge of communication and design.

When starting to prepare a new Event share this doc with the appropiate people.
