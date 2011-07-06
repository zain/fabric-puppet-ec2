from fabric.api import *
from fabric.decorators import *
from ec2 import get_puppetmaster
from utils import *

@task
def install_master():
    """The same thing as create_master but without creating a new instance."""
    btw("Installing puppetmaster...")
    sudo("aptitude install -q -y puppetmaster")


@task
def install_slave(master=None):
    """Sets up a single existing slave server in the same way create_slaves would."""

    master = get_puppetmaster(master)

    btw("Installing puppet...")

    with settings(hide('stdout'), show('running')):
        sudo("aptitude install -q -y puppet")
