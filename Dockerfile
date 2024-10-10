FROM lambci/lambda:build-python3.8

RUN python -m venv /opt/ve

COPY requirements.txt .
COPY requirements-dev.txt .
COPY requirements-freeze.txt .
RUN . /opt/ve/bin/activate && pip install -r requirements-freeze.txt

# Fancy prompt to remind you are in zappashell
RUN echo 'export PS1="\[\e[36m\]zappashell>\[\e[m\] "' >> /root/.bashrc

ADD . /var/task
WORKDIR /var/task
CMD . /opt/ve/bin/activate && bash 
