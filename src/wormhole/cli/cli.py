from __future__ import print_function

import os
import time
start = time.time()
import traceback
from textwrap import fill, dedent
from sys import stdout, stderr
from . import public_relay
from .. import __version__
from ..timing import DebugTiming
from ..errors import WrongPasswordError, WelcomeError, KeyFormatError
from twisted.internet.defer import inlineCallbacks, maybeDeferred
from twisted.internet.task import react

import click
top_import_finish = time.time()


class Config(object):
    """
    Union of config options that we pass down to (sub) commands.
    """
    def __init__(self):
        # This only holds attributes which are *not* set by CLI arguments.
        # Everything else comes from Click decorators, so we can be sure
        # we're exercising the defaults.
        self.timing = DebugTiming()
        self.cwd = os.getcwd()
        self.stdout = stdout
        self.stderr = stderr

def _compose(*decorators):
    def decorate(f):
        for d in reversed(decorators):
            f = d(f)
        return f
    return decorate


ALIASES = {
    "tx": "send",
    "rx": "receive",
}
class AliasedGroup(click.Group):
    def get_command(self, ctx, cmd_name):
        cmd_name = ALIASES.get(cmd_name, cmd_name)
        return click.Group.get_command(self, ctx, cmd_name)


TopArgs = _compose(
    click.option("-c", "--code-length", default=2, metavar="NUMWORDS",
                 help="length of code (in bytes/words)",
                 ),
    click.option("-v", "--verify", is_flag=True, default=False,
                 help="display verification string (and wait for approval)",
                 ),
    click.option("--hide-progress", is_flag=True, default=False,
                 help="supress progress-bar display",
                 ),
    click.option("--no-listen", is_flag=True, default=False,
                 help="(debug) don't open a listening socket for Transit",
                 ),
    click.option("--tor", is_flag=True, default=False,
                 help="use Tor when connecting",
                 ),
)

# top-level command ("wormhole ...")
@click.group(cls=AliasedGroup)
@TopArgs
@click.option(
    "--relay-url", default=public_relay.RENDEZVOUS_RELAY,
    metavar="URL",
    help="rendezvous relay to use",
)
@click.option(
    "--transit-helper", default=public_relay.TRANSIT_RELAY,
    metavar="tcp:HOST:PORT",
    help="transit relay to use",
)
@click.option(
    "--dump-timing", type=type(u""), # TODO: hide from --help output
    default=None,
    metavar="FILE.json",
    help="(debug) write timing data to file",
)
@click.version_option(
    message="magic-wormhole %(version)s",
    version=__version__,
)
@click.pass_context
def wormhole(context, tor, no_listen, dump_timing, hide_progress,
             verify, code_length, transit_helper, relay_url):
    """
    Create a Magic Wormhole and communicate through it.

    Wormholes are created by speaking the same magic CODE in two
    different places at the same time.  Wormholes are secure against
    anyone who doesn't use the same code.
    """
    context.obj = cfg = Config()
    cfg.tor = tor
    cfg.listen = not no_listen
    cfg.relay_url = relay_url
    cfg.transit_helper = transit_helper
    cfg.code_length = code_length
    cfg.verify = verify
    cfg.hide_progress = hide_progress
    cfg.dump_timing = dump_timing


@inlineCallbacks
def _dispatch_command(reactor, cfg, command):
    """
    Internal helper. This calls the given command (a no-argument
    callable) with the Config instance in cfg and interprets any
    errors for the user.
    """
    cfg.timing.add("command dispatch")
    cfg.timing.add("import", when=start, which="top").finish(when=top_import_finish)

    try:
        yield maybeDeferred(command)
    except WrongPasswordError as e:
        msg = fill("ERROR: " + dedent(e.__doc__))
        print(msg, file=stderr)
    except WelcomeError as e:
        msg = fill("ERROR: " + dedent(e.__doc__))
        print(msg, file=stderr)
        print(file=stderr)
        print(str(e), file=stderr)
    except KeyFormatError as e:
        msg = fill("ERROR: " + dedent(e.__doc__))
        print(msg, file=stderr)
    except Exception as e:
        traceback.print_exc()
        print("ERROR:", e, file=stderr)
        raise SystemExit(1)

    cfg.timing.add("exit")
    if cfg.dump_timing:
        cfg.timing.write(cfg.dump_timing, stderr)


CommonArgs = _compose(
    click.option("-0", "zeromode", default=False, is_flag=True,
                 help="enable no-code anything-goes mode",
                 ),
)

# wormhole send (or "wormhole tx")
@wormhole.command()
@CommonArgs
@click.option(
    "--code", metavar="CODE",
    help="human-generated code phrase",
)
@click.option(
    "--text", default=None, metavar="MESSAGE",
    help="text message to send, instead of a file. Use '-' to read from stdin.",
)
@click.argument("what", required=False)
@click.pass_obj
def send(cfg, **kwargs):
    """Send a text message, file, or directory"""
    for name, value in kwargs.items():
        setattr(cfg, name, value)
    with cfg.timing.add("import", which="cmd_send"):
        from . import cmd_send

    return go(cmd_send.send, cfg)

# this intermediate function can be mocked by tests that need to build a
# Config object
def go(f, cfg):
    # note: react() does not return
    return react(_dispatch_command, (cfg, lambda: f(cfg)))


# wormhole receive (or "wormhole rx")
@wormhole.command()
@CommonArgs
@click.option(
    "--only-text", "-t", is_flag=True,
    help="refuse file transfers, only accept text transfers",
)
@click.option(
    "--accept-file", is_flag=True,
    help="accept file transfer without asking for confirmation",
)
@click.option(
    "--output-file", "-o",
    metavar="FILENAME|DIRNAME",
    help=("The file or directory to create, overriding the name suggested"
          " by the sender."),
)
@click.argument(
    "code", nargs=-1, default=None,
#    help=("The magic-wormhole code, from the sender. If omitted, the"
#          " program will ask for it, using tab-completion."),
)
@click.pass_obj
def receive(cfg, code, **kwargs):
    """
    Receive a text message, file, or directory (from 'wormhole send')
    """
    for name, value in kwargs.items():
        setattr(cfg, name, value)
    with cfg.timing.add("import", which="cmd_receive"):
        from . import cmd_receive
    if len(code) == 1:
        cfg.code = code[0]
    elif len(code) > 1:
        print(
            "Pass either no code or just one code; you passed"
            " {}: {}".format(len(code), ', '.join(code))
        )
        raise SystemExit(1)
    else:
        cfg.code = None

    return go(cmd_receive.receive, cfg)
