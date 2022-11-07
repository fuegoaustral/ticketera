# Ticketera de FA

## Deploy

### OS X

Setup aws credentials in `[default]` (needed in zappa_settings.json). Then run:

```
$ docker build . -t ticketera-zappashell
$ alias zappashell='docker run -ti -e AWS_PROFILE=default -v "$(pwd):/var/task" -v ~/.aws/:/root/.aws --rm ticketera-zappashell'
$ zappashell
> zappa update dev
```
