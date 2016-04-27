# -*- coding: utf-8 -*-
import traceback
import paramiko
from paramiko.rsakey import RSAKey
from io import StringIO


def force_unicode(s, encoding='utf-8', errors='strict'):
    """
    Similar to smart_text, except that lazy instances are resolved to
    strings, rather than kept as lazy objects.

    If strings_only is True, don't convert (some) non-string-like objects.
    """
    # Handle the common case first for performance reasons.
    if isinstance(s, unicode):
        return s
    if not isinstance(s, str):
        return unicode(s)
    return s.decode(encoding, errors)


DEFAULT_ENCODING = "utf-8"


class ErrorReturnCode(Exception):
    def __init__(self, full_cmd, stdout, stderr, exit_code, hostname, username, port):
        self.hostname = hostname
        self.username = username
        self.port = port
        self.full_cmd = full_cmd
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        tstdout = self.stdout if self.stdout is not None else "<redirected>"
        tstderr = self.stderr if self.stderr is not None else "<redirected>"
        self.message = None
        super(ErrorReturnCode, self).__init__(full_cmd, stdout, stderr, exit_code, hostname, username, port)

    def __str__(self):
        return self.message

    def __repr__(self):
        return 'ErrorReturnCode(%s)' % self.message


class RunStatus(object):
    """ Объект, возвращаемый командой run """

    def __init__(self, server, command, stdout, stderr, returncode):
        self.server = server
        self.command = command
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.succeeded = self.returncode == 0

    def get_unicode_version(self, errors='strict'):
        return RunStatus(
            force_unicode(self.server, errors=errors),
            force_unicode(self.command, errors=errors),
            force_unicode(self.stdout, errors=errors),
            force_unicode(self.stderr, errors=errors),
            self.returncode
        )

    def __str__(self):
        return self.stdout


class SSHConnection(object):
    def __init__(self, hostname, username, password=None, forward_ssh_agent=False,
                 identityfile=None, reuse_connection=True, is_raise=True,
                 pool=None, port=22, config_path=None, timeout=10, pkey=None):
        self.hostname = hostname
        self.username = username
        self.password = password
        self.port = port

        self.config_path = config_path
        self.timeout = timeout
        self.forward_ssh_agent = forward_ssh_agent
        self.reuse_connection = reuse_connection
        self.identityfile = identityfile
        self.is_raise = is_raise

        import logging
        self.logger = logging.getLogger(__file__)
        self.pool = pool
        self.last_command = None
        self.ssh = None

        self.pkey = pkey

    @property
    def pool_key(self):
        return self.pool.make_pool_key(self.hostname, self.username, self.port, self.forward_ssh_agent)

    @property
    def is_connected(self):
        return self.ssh and self.ssh.get_transport() and self.ssh.get_transport().active

    @property
    def server_name(self):
        return "{username}@{hostname}:{port}".format(hostname=self.hostname, port=self.port, username=self.username)

    def run(self, command, _in=None, _sudo=False, interpreter=None, retry=True, _name=None, send_fail_notice=True,
            _locale=True):

        if interpreter:
            command = '''%s <<'ENDOFMESSAGE'\n%s\nENDOFMESSAGE''' % (interpreter, command)

        # На всякий случай сбрасываем локаль
        if _locale:
            command = "LANG=C " + command

        # Переоткрываем соединение, если требуется
        if not self.is_connected:
            self.open()

        channel = self.ssh
        if self.forward_ssh_agent:
            transport = self.ssh.get_transport()
            # set window size 1Mb (default 64k)
            transport.window_size = 1024**2
            agent_channel = transport.open_session()
            paramiko.agent.AgentRequestHandler(agent_channel)
            channel = agent_channel

        # Выполняем команду
        try:
            self.logger.debug(u"SSH command '{name}' is executed on server {server}".format(
                              name=command, server=self.server_name))
            result = channel.exec_command(command)
            if self.forward_ssh_agent:
                result = channel.makefile('wb'), channel.makefile('rb'), channel.makefile_stderr()
            stdin, stdout, stderr = result
            if _in is not None:
                stdin.write(_in)
                stdin.channel.shutdown_write()
        except Exception as e:
            if retry:
                self.logger.warn(u"SSH command '{name}' is repeated execution on server {server}. Reason: {ex}".format(
                    name=command, server=self.server_name, ex=e.__class__.__name__))
                self.close()
                return super(self.__class__, self).run(self.last_command, _in, _sudo, interpreter, retry=False)

            self.logger.error(u"SSH command '{name}' on {server} is completed with exception {ex}".format(
                name=command, server=self.server_name, ex=e.__class__.__name__))
            if self.is_raise:
                raise
            status = self.error_to_status(e, send_fail_notice=send_fail_notice)
        else:
            error_code = stdout.channel.recv_exit_status()
            if error_code:
                self.logger.error(u"SSH command '{name}' is completed with error on server {server} with code {code}"
                                  .format(name=command, server=self.server_name, code=error_code))
                if self.is_raise:
                    raise ErrorReturnCode(command, stdout.read(), stderr.read(), stdout.channel.recv_exit_status(),
                                          self.hostname, self.username, self.port)
            else:
                self.logger.info(u"SSH command '{name}' is successfull executed on server {server}".format(
                    name=command, server=self.server_name))
            status = self.result_to_status(result, send_fail_notice=send_fail_notice)
        finally:
            self.last_command = None
        return status

    def open(self):
        """ Открываем низкоуровневое соединение """
        self.logger.debug(u'Open SSH connection {server} (reuse={reuse})'.format(
            server=self.server_name, reuse=self.reuse_connection))

        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        if self.pkey:
            rsakey = RSAKey.from_private_key(StringIO(self.pkey))
            ssh.connect(hostname=self.hostname, port=self.port, username=self.username,
                        pkey=rsakey, timeout=self.timeout)
        else:
            ssh.connect(hostname=self.hostname, port=self.port, username=self.username,
                        password=self.password,
                        timeout=self.timeout)


        ssh.get_transport().window_size = 1024**2

        # Возвращаем соединение
        self.ssh = ssh

    def close(self):
        if self.ssh:
            self.logger.debug(u'Close SSH connection {server} (reuse={reuse})'.format(
                server=self.server_name, reuse=self.reuse_connection))

            self.ssh.close()
            self.ssh = None

    def open_sftp(self):
        # Переоткрываем соединение, если требуется
        if not self.is_connected:
            self.open()
        return self.ssh.open_sftp()

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        if not self.reuse_connection:
            self.close()

    def __del__(self):
        if not self.reuse_connection:
            self.close()

    def error_to_status(self, error, send_fail_notice=True):
        status = RunStatus(
            server=self.hostname,
            command=self.last_command,
            stdout=str(error),
            stderr=str(traceback.format_exc()),
            returncode=1,
        )
        return status

    def result_to_status(self, statuses, send_fail_notice=True):
        _, stdout, stderr = statuses
        stdout_data = stdout.read().strip()
        stderr_data = stderr.read().strip()
        returncode = stdout.channel.recv_exit_status()
        status = RunStatus(
            server=self.hostname,
            command=self.last_command,
            stdout=stdout_data,
            stderr=stderr_data,
            returncode=returncode
        )
        return status

    def upload_file(self, file_obj, target, mode=None, owner=None):
        """ Безопасная загрузка файла на сервер """
        tempname = self.run('mktemp')
        if not isinstance(tempname, (str, unicode)):
            tempname = tempname.stdout.strip()
        tempname = force_unicode(tempname)
        logger = self.pool.logger
        sftp = self.open_sftp()
        target = force_unicode(target)

        # копируем на сервер во временный файл
        logger.debug((u'SSH safe upload: loading file stream to location {target} on server {server} use temporary '
                      u'file {tempname}').format(server=self.server_name, target=target, tempname=tempname))

        # перемещаем временный файл в его постоянное расположение
        try:
            sftp.putfo(file_obj, tempname)
            sftp.close()

            logger.debug(u'SSH safe upload: rename temporary file {tempname} to {server}{target}'
                         .format(server=self.server_name, target=target, tempname=tempname))
            self.run(u'mv -f {0} {1}'.format(tempname, target))

            if mode:
                logger.debug(u'SSH safe upload: Change mode for {server}{target} to {mode}'.format(
                    server=self.server_name, target=target, mode=mode))
                self.run(u'chmod {0:o} {1}'.format(mode, target))

            if owner:
                owner = force_unicode(owner)
                logger.debug(u'SSH safe upload: Change owner for {server}{target} to {owner}'.format(
                    server=self.server_name, target=target, owner=owner))
                self.run(u'chown {0} {1}'.format(owner, target))
        except:
            logger.error(u"SSH upload file: loading file {server}{target} is failed".format(
                server=self.server_name, target=target), exc_info=1)
            raise
        finally:
            self.run(u'rm -f {0}'.format(tempname))
