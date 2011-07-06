import time
from fabric.colors import *
from fabric.utils import *

# some functions to help with logging
def fyi(msg): print >>sys.stderr, msg
def btw(msg): print >>sys.stderr, blue(msg)
def yay(msg): print >>sys.stderr, green(msg)
def err(msg): print >>sys.stderr, red(msg, bold=True)
def die(msg): abort(red(msg, bold=True))
def wait(msg=". "):
    print >>sys.stderr, msg,
    time.sleep(2)
